import logging
from collections import defaultdict
from typing import Dict, List, Optional, Union

import numpy as np
import torch
import torch.optim as optim
from numpy import ndarray

from emmental import Meta
from emmental.data import EmmentalDataLoader
from emmental.logging import LoggingManager
from emmental.model import EmmentalModel
from emmental.optimizers.bert_adam import BertAdam
from emmental.schedulers import SCHEDULERS
from emmental.utils.utils import construct_identifier, prob_to_pred

try:
    from IPython import get_ipython

    if "IPKernelApp" not in get_ipython().config:
        raise ImportError("console")
except (AttributeError, ImportError):
    from tqdm import tqdm
else:
    from tqdm import tqdm_notebook as tqdm

logger = logging.getLogger(__name__)


class EmmentalLearner(object):
    r"""A class for emmental multi-task learning.

    Args:
      name(str, optional): Name of the learner, defaults to None.

    """

    def __init__(self, name: Optional[str] = None) -> None:
        self.name = name if name is not None else type(self).__name__

    def _set_logging_manager(self) -> None:
        r"""Set logging manager."""

        self.logging_manager = LoggingManager(self.n_batches_per_epoch)

    def _set_optimizer(self, model: EmmentalModel) -> None:
        r"""Set optimizer for learning process.

        Args:
          model(EmmentalModel): The model to set up the optimizer.

        """

        # TODO: add more optimizer support and fp16
        optimizer_config = Meta.config["learner_config"]["optimizer_config"]
        opt = optimizer_config["optimizer"]

        parameters = filter(lambda p: p.requires_grad, model.parameters())

        if opt == "sgd":
            optimizer = optim.SGD(
                parameters,
                lr=optimizer_config["lr"],
                weight_decay=optimizer_config["l2"],
                **optimizer_config["sgd_config"],
            )
        elif opt == "adam":
            optimizer = optim.Adam(
                parameters,
                lr=optimizer_config["lr"],
                weight_decay=optimizer_config["l2"],
                **optimizer_config["adam_config"],
            )
        elif opt == "adamax":
            optimizer = optim.Adamax(
                parameters,
                lr=optimizer_config["lr"],
                weight_decay=optimizer_config["l2"],
                **optimizer_config["adamax_config"],
            )
        elif opt == "bert_adam":
            optimizer = BertAdam(
                parameters,
                lr=optimizer_config["lr"],
                weight_decay=optimizer_config["l2"],
                **optimizer_config["bert_adam_config"],
            )
        else:
            raise ValueError(f"Unrecognized optimizer option '{opt}'")

        logger.info(f"Using optimizer {optimizer}")

        self.optimizer = optimizer

    def _set_lr_scheduler(self, model: EmmentalModel) -> None:
        r"""Set learning rate scheduler for learning process.

        Args:
          model(EmmentalModel): The model to set up lr scheduler.

        """

        # Set warmup scheduler
        self._set_warmup_scheduler(model)

        # Set lr scheduler
        # TODO: add more lr scheduler support
        opt = Meta.config["learner_config"]["lr_scheduler_config"]["lr_scheduler"]
        lr_scheduler_config = Meta.config["learner_config"]["lr_scheduler_config"]

        if opt is None:
            lr_scheduler = None
        elif opt == "linear":
            total_steps = (
                self.n_batches_per_epoch * Meta.config["learner_config"]["n_epochs"]
            )
            linear_decay_func = lambda x: (total_steps - self.warmup_steps - x) / (
                total_steps - self.warmup_steps
            )
            lr_scheduler = optim.lr_scheduler.LambdaLR(
                self.optimizer, linear_decay_func
            )
        elif opt == "exponential":
            lr_scheduler = optim.lr_scheduler.ExponentialLR(
                self.optimizer, **lr_scheduler_config["exponential_config"]
            )
        elif opt == "step":
            lr_scheduler = optim.lr_scheduler.StepLR(
                self.optimizer, **lr_scheduler_config["step_config"]
            )
        elif opt == "multi_step":
            lr_scheduler = optim.lr_scheduler.MultiStepLR(
                self.optimizer, **lr_scheduler_config["multi_step_config"]
            )
        # elif opt == "reduce_on_plateau":
        #     lr_scheduler = optim.lr_scheduler.ReduceLROnPlateau(
        #         self.optimizer,
        #         min_lr=lr_scheduler_config["min_lr"],
        #         **lr_scheduler_config["plateau_config"],
        #     )
        else:
            raise ValueError(f"Unrecognized lr scheduler option '{opt}'")

        self.lr_scheduler = lr_scheduler

    def _set_warmup_scheduler(self, model: EmmentalModel) -> None:
        r"""Set warmup learning rate scheduler for learning process.

        Args:
          model(EmmentalModel): The model to set up warmup scheduler.

        """

        self.warmup_steps = 0
        if Meta.config["learner_config"]["lr_scheduler_config"]["warmup_steps"]:
            warmup_steps = Meta.config["learner_config"]["lr_scheduler_config"][
                "warmup_steps"
            ]
            if warmup_steps < 0:
                raise ValueError(f"warmup_steps much greater or equal than 0.")
            warmup_unit = Meta.config["learner_config"]["lr_scheduler_config"][
                "warmup_unit"
            ]
            if warmup_unit == "epoch":
                self.warmup_steps = int(warmup_steps * self.n_batches_per_epoch)
            elif warmup_unit == "batch":
                self.warmup_steps = int(warmup_steps)
            else:
                raise ValueError(
                    f"warmup_unit must be 'batch' or 'epoch', but {warmup_unit} found."
                )
            linear_warmup_func = lambda x: x / self.warmup_steps
            warmup_scheduler = optim.lr_scheduler.LambdaLR(
                self.optimizer, linear_warmup_func
            )
            logger.info(f"Warmup {self.warmup_steps} batchs.")
        elif Meta.config["learner_config"]["lr_scheduler_config"]["warmup_percentage"]:
            warmup_percentage = Meta.config["learner_config"]["lr_scheduler_config"][
                "warmup_percentage"
            ]
            self.warmup_steps = int(
                warmup_percentage
                * Meta.config["learner_config"]["n_epochs"]
                * self.n_batches_per_epoch
            )
            linear_warmup_func = lambda x: x / self.warmup_steps
            warmup_scheduler = optim.lr_scheduler.LambdaLR(
                self.optimizer, linear_warmup_func
            )
            logger.info(f"Warmup {self.warmup_steps} batchs.")
        else:
            warmup_scheduler = None

        self.warmup_scheduler = warmup_scheduler

    def _update_lr_scheduler(self, model: EmmentalModel, step: int) -> None:
        r"""Update the lr using lr_scheduler with each batch.

        Args:
          model(EmmentalModel): The model to update lr scheduler.
          step(int): The current step.

        """

        if self.warmup_scheduler and step < self.warmup_steps:
            self.warmup_scheduler.step()
        elif self.lr_scheduler is not None:
            opt = Meta.config["learner_config"]["lr_scheduler_config"]["lr_scheduler"]
            if opt in ["linear", "exponential"]:
                self.lr_scheduler.step()
            elif (
                opt in ["step", "multi_step"]
                and step > 0
                and (step + 1) % self.n_batches_per_epoch == 0
            ):
                self.lr_scheduler.step()
            min_lr = Meta.config["learner_config"]["lr_scheduler_config"]["min_lr"]
            if min_lr and self.optimizer.param_groups[0]["lr"] < min_lr:
                self.optimizer.param_groups[0]["lr"] = min_lr

    def _set_task_scheduler(self) -> None:
        r"""Set task scheduler for learning process"""

        opt = Meta.config["learner_config"]["task_scheduler_config"]["task_scheduler"]

        if opt in ["sequential", "round_robin", "mixed"]:
            self.task_scheduler = SCHEDULERS[opt](
                **Meta.config["learner_config"]["task_scheduler_config"][
                    f"{opt}_scheduler_config"
                ]
            )
        else:
            raise ValueError(f"Unrecognized task scheduler option '{opt}'")

    def _evaluate(
        self,
        model: EmmentalModel,
        dataloaders: List[EmmentalDataLoader],
        split: Union[List[str], str],
    ) -> Dict[str, float]:
        r"""Evaluate the model.

        Args:
          model(EmmentalModel): The model to evaluate.
          dataloaders(List[EmmentalDataLoader]): The data to evaluate.
          split(str): The split to evaluate.

        Returns:
          dict: The score dict.

        """

        if not isinstance(split, list):
            valid_split = [split]
        else:
            valid_split = split

        valid_dataloaders = [
            dataloader for dataloader in dataloaders if dataloader.split in valid_split
        ]
        return model.score(valid_dataloaders)

    def _logging(
        self,
        model: EmmentalModel,
        dataloaders: List[EmmentalDataLoader],
        batch_size: int,
    ) -> Dict[str, float]:
        r"""Checking if it's time to evaluting or checkpointing.

        Args:
          model(EmmentalModel): The model to log.
          dataloaders(List[EmmentalDataLoader]): The data to evaluate.
          batch_size(int): Batch size.

        Returns:
          dict: The score dict.

        """

        # Switch to eval mode for evaluation
        model.eval()

        metric_dict = dict()

        self.logging_manager.update(batch_size)

        # Log the loss and lr
        metric_dict.update(self._aggregate_running_metrics(model))

        # Evaluate the model and log the metric
        trigger_evaluation = self.logging_manager.trigger_evaluation()
        if trigger_evaluation:

            # Log task specific metric
            metric_dict.update(
                self._evaluate(
                    model, dataloaders, Meta.config["learner_config"]["valid_split"]
                )
            )

            self.logging_manager.write_log(metric_dict)

            self._reset_losses()

        # Log metric dict every trigger evaluation time or full epoch
        if trigger_evaluation or self.logging_manager.epoch_total == int(
            self.logging_manager.epoch_total
        ):
            logger.info(
                f"{self.logging_manager.counter_unit.capitalize()}: "
                f"{self.logging_manager.unit_total:.2f} {metric_dict}"
            )

        # Checkpoint the model
        if self.logging_manager.trigger_checkpointing():
            self.logging_manager.checkpoint_model(
                model, self.optimizer, self.lr_scheduler, metric_dict
            )

            self.logging_manager.write_log(metric_dict)

            self._reset_losses()

        # Switch to train mode
        model.train()

        return metric_dict

    def _aggregate_running_metrics(self, model: EmmentalModel) -> Dict[str, float]:
        r"""Calculate the running overall and task specific metrics.

        Args:
          model(EmmentalModel): The model to evaluate.

        Returns:
          dict: The score dict.

        """

        metric_dict = dict()

        total_count = 0
        # Log task specific loss
        for identifier in self.running_uids.keys():
            count = len(self.running_uids[identifier])
            if count > 0:
                metric_dict[identifier + "/loss"] = (
                    self.running_losses[identifier] / count
                )
            total_count += count

        # Calculate average micro loss
        if total_count > 0:
            total_loss = sum(self.running_losses.values())
            metric_dict["model/all/train/loss"] = total_loss / total_count

        micro_score_dict: Dict[str, List[ndarray]] = defaultdict(list)
        macro_score_dict: Dict[str, List[ndarray]] = defaultdict(list)

        # Calculate training metric
        for identifier in self.running_uids.keys():
            task_name, data_name, split = identifier.split("/")

            metric_score = model.scorers[task_name].score(
                self.running_golds[identifier],
                self.running_probs[identifier],
                prob_to_pred(self.running_probs[identifier]),
                self.running_uids[identifier],
            )
            for metric_name, metric_value in metric_score.items():
                metric_dict[f"{identifier}/{metric_name}"] = metric_value

            # Collect average score
            identifier = construct_identifier(task_name, data_name, split, "average")

            metric_dict[identifier] = np.mean(list(metric_score.values()))

            micro_score_dict[split].extend(list(metric_score.values()))
            macro_score_dict[split].append(metric_dict[identifier])

        # Collect split-wise micro/macro average score
        for split in micro_score_dict.keys():
            identifier = construct_identifier("model", "all", split, "micro_average")
            metric_dict[identifier] = np.mean(micro_score_dict[split])
            identifier = construct_identifier("model", "all", split, "macro_average")
            metric_dict[identifier] = np.mean(macro_score_dict[split])

        # Log the learning rate
        metric_dict["model/all/train/lr"] = self.optimizer.param_groups[0]["lr"]

        return metric_dict

    def _reset_losses(self) -> None:
        r"""Reset running logs."""

        self.running_uids: Dict[str, List[str]] = defaultdict(list)
        self.running_losses: Dict[str, ndarray] = defaultdict(float)
        self.running_probs: Dict[str, List[ndarray]] = defaultdict(list)
        self.running_golds: Dict[str, List[ndarray]] = defaultdict(list)

    def learn(
        self, model: EmmentalModel, dataloaders: List[EmmentalDataLoader]
    ) -> None:
        r"""The learning procedure of emmental MTL.

        Args:
          model(EmmentalModel): The emmental model that needs to learn.
          dataloaders(List[EmmentalDataLoader]): a list of dataloaders used to
            learn the model.

        """

        # Generate the list of dataloaders for learning process
        train_split = Meta.config["learner_config"]["train_split"]
        if isinstance(train_split, str):
            train_split = [train_split]

        train_dataloaders = [
            dataloader for dataloader in dataloaders if dataloader.split in train_split
        ]

        if not train_dataloaders:
            raise ValueError(
                f"Cannot find the specified train_split "
                f'{Meta.config["learner_config"]["train_split"]} in dataloaders.'
            )

        # Set up task_scheduler
        self._set_task_scheduler()

        # Calculate the total number of batches per epoch
        self.n_batches_per_epoch = self.task_scheduler.get_num_batches(
            train_dataloaders
        )

        # Set up logging manager
        self._set_logging_manager()
        # Set up optimizer
        self._set_optimizer(model)
        # Set up lr_scheduler
        self._set_lr_scheduler(model)

        # Set to training mode
        model.train()

        logger.info(f"Start learning...")

        self.metrics: Dict[str, float] = dict()
        self._reset_losses()

        for epoch_num in range(Meta.config["learner_config"]["n_epochs"]):
            batches = tqdm(
                enumerate(self.task_scheduler.get_batches(train_dataloaders)),
                total=self.n_batches_per_epoch,
                disable=(not Meta.config["meta_config"]["verbose"]),
                desc=f"Epoch {epoch_num}:",
            )

            for batch_num, batch in batches:

                # Covert single batch into a batch list
                if not isinstance(batch, list):
                    batch = [batch]

                total_batch_num = epoch_num * self.n_batches_per_epoch + batch_num
                batch_size = 0

                # Set gradients of all model parameters to zero
                self.optimizer.zero_grad()

                for uids, X_dict, Y_dict, task_to_label_dict, data_name, split in batch:
                    batch_size += len(next(iter(Y_dict.values())))

                    # Perform forward pass and calcualte the loss and count
                    uid_dict, loss_dict, prob_dict, gold_dict = model(
                        uids, X_dict, Y_dict, task_to_label_dict
                    )

                    # Update running loss and count
                    for task_name in uid_dict.keys():
                        identifier = f"{task_name}/{data_name}/{split}"
                        self.running_uids[identifier].extend(uid_dict[task_name])
                        self.running_losses[identifier] += loss_dict[
                            task_name
                        ].item() * len(uid_dict[task_name])
                        self.running_probs[identifier].extend(prob_dict[task_name])
                        self.running_golds[identifier].extend(gold_dict[task_name])

                    # Skip the backward pass if no loss is calcuated
                    if not loss_dict:
                        continue

                    # Calculate the average loss
                    loss = sum(
                        [
                            model.weights[task_name] * task_loss
                            for task_name, task_loss in loss_dict.items()
                        ]
                    )

                    # Perform backward pass to calculate gradients
                    loss.backward()

                # Clip gradient norm
                if Meta.config["learner_config"]["optimizer_config"]["grad_clip"]:
                    torch.nn.utils.clip_grad_norm_(
                        model.parameters(),
                        Meta.config["learner_config"]["optimizer_config"]["grad_clip"],
                    )

                # Update the parameters
                self.optimizer.step()

                # Update lr using lr scheduler
                self._update_lr_scheduler(model, total_batch_num)

                self.metrics.update(self._logging(model, dataloaders, batch_size))

                batches.set_postfix(self.metrics)

        model = self.logging_manager.close(model)
