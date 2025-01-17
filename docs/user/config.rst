Configuring Emmental
====================

By default, Emmental_ loads the default config ``.emmental-default-config.yaml``
from the Emmental_ directory, and loads the user defined config
``emmental-config.yaml`` starting from the current working directory, allowing you
to have multiple configuration files for different directories or projects. If it's
not there, it looks in parent directories. If no file is found, a default
configuration will be used.

Emmental will only ever use one ``.emmental-config.yaml`` file. It does not look
for multiple files and will not compose configuration settings from different
files.

The default ``.emmental-config.yaml`` configuration file is shown below::

    # Meta configuration
    meta_config:
        seed: 0  # random seed for all numpy/torch/cuda operations in model and learning
        verbose: True # whether to print the log information
        log_path: # log directory

    # Data configuration
    data_config:
        min_data_len: 0 # min data length
        max_data_len: 0 # max data length (e.g., 0 for no max_len)

    # Model configuration
    model_config:
        model_path: # path to pretrained model
        device: 0 # -1 for cpu or gpu id (e.g., 0 for cuda:0)
        dataparallel: True # whether to use dataparallel or not

    # Learning configuration
    learner_config:
        fp16: False # whether to use half precision
        n_epochs: 1 # total number of learning epochs
        train_split: train # the split for training, accepts str or list of strs
        valid_split: valid # the split for validation, accepts str or list of strs
        test_split: test # the split for testing, accepts str or list of strs
        ignore_index: 0 # the ignore index, uses for masking samples
        optimizer_config:
            optimizer: adam # [sgd, adam, adamax, bert_adam]
            lr: 0.001 # Learing rate
            l2: 0.0 # l2 regularization
            grad_clip: 1.0 # gradient clipping
            sgd_config:
                momentum: 0.9
            adam_config:
                betas: !!python/tuple [0.9, 0.999]
                eps: 0.00000001
                amsgrad: False
            adamax_config:
                betas: !!python/tuple [0.9, 0.999]
                eps: 0.00000001
            bert_adam_config:
                betas: !!python/tuple [0.9, 0.999]
                eps: 0.00000001
        lr_scheduler_config:
            lr_scheduler: # [linear, exponential, reduce_on_plateau]
            warmup_steps: # warm up steps
            warmup_unit: batch # [epoch, batch]
            warmup_percentage: # warm up percentage
            min_lr: 0.0 # minimum learning rate
            linear_config:
                min_lr: 0.0
            exponential_config:
                gamma: 0.9
            plateau_config:
                factor: 0.5
                patience: 10
                threshold: 0.0001
            step_config:
                step_size: 1
                gamma: 0.1
                last_epoch: -1
            multi_step_config:
                milestones:
                    - 1000
                gamma: 0.1
                last_epoch: -1
        task_scheduler_config:
            task_scheduler: round_robin # [sequential, round_robin, mixed]
            sequential_scheduler_config:
                fillup: False
            round_robin_scheduler_config:
                fillup: False
            mixed_scheduler_config:
                fillup: False
        global_evaluation_metric_dict: # global evaluation metric dict

    # Logging configuration
    logging_config:
        counter_unit: epoch # [epoch, batch]
        evaluation_freq: 2
        writer_config:
            writer: tensorboard # [json, tensorboard]
            verbose: True
        checkpointing: False
        checkpointer_config:
            checkpoint_path:
            checkpoint_freq: 1
            checkpoint_metric: # metric_name: mode, where mode in [min, max]
                # model/train/all/loss: min
            checkpoint_task_metrics: # task_metric_name: mode
            checkpoint_runway: 0 # checkpointing runway (no checkpointing before k unit)
            clear_intermediate_checkpoints: True # whether to clear intermediate checkpoints
            clear_all_checkpoints: False # whether to clear all checkpoints

User can also use the Emmental_ utility function ``parse_arg`` and
``parse_arg_to_config`` from ``emmental.utils`` to generate the config object.

.. _Emmental: https://github.com/SenWu/Emmental