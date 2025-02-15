import numpy as np
import torch
from torch.nn.utils import clip_grad_norm_
from base import BaseTrainer
from utils import inf_loop, MetricTracker


class Trainer(BaseTrainer):
    """
    Trainer class
    """
    def __init__(self, model, criterion, metric_ftns, optimizer, config, device,
                 data_loader, valid_data_loader=None, lr_scheduler=None, len_epoch=None):
        super().__init__(model, criterion, metric_ftns, optimizer, config, device)
        self.config = config
        self.data_loader = data_loader
        if len_epoch is None:
            # epoch-based training
            self.len_epoch = len(self.data_loader)
        else:
            # iteration-based training
            self.data_loader = inf_loop(data_loader)
            self.len_epoch = len_epoch
        self.valid_data_loader = valid_data_loader
        self.do_validation = self.valid_data_loader is not None
        self.lr_scheduler = lr_scheduler
        self.log_step = int(np.sqrt(data_loader.batch_size))

        self.train_metrics = MetricTracker('loss', *[m.__name__ for m in self.metric_ftns], writer=self.writer)
        self.valid_metrics = MetricTracker('loss', *[m.__name__ for m in self.metric_ftns], writer=self.writer)

    def _train_epoch(self, epoch):
        """
        Training logic for an epoch

        :param epoch: Integer, current training epoch.
        :return: A log that contains average loss and metric in this epoch.
        """
        self.model.train()
        self.train_metrics.reset()
        for batch_idx, (sentences, sentences_mask, strokes, strokes_mask) in enumerate(self.data_loader):

            # Moving input data to device
            sentences, sentences_mask = sentences.to(self.device), sentences_mask.to(self.device)
            strokes, strokes_mask = strokes.to(self.device), strokes_mask.to(self.device)

            # Compute the loss and perform an optimization step
            self.optimizer.zero_grad()

            if str(self.model).startswith('Unconditional'):
                output_network = self.model(sentences, sentences_mask, strokes, strokes_mask)
                gaussian_params = self.model.compute_gaussian_parameters(output_network)
                loss = self.criterion(gaussian_params, strokes, strokes_mask)
                loss.backward()
                # Gradient clipping
                clip_grad_norm_(self.model.rnn_1.parameters(), 10)
                clip_grad_norm_(self.model.rnn_2.parameters(), 10)
                clip_grad_norm_(self.model.rnn_3.parameters(), 10)

            elif str(self.model).startswith('Conditional'):
                output_network = self.model(sentences, sentences_mask, strokes, strokes_mask)
                gaussian_params = self.model.compute_gaussian_parameters(output_network)
                loss = self.criterion(gaussian_params, strokes, strokes_mask)
                loss.backward()
                # Gradient clipping
                clip_grad_norm_(self.model.rnn_1_with_gaussian_attention.lstm_cell.parameters(), 10)
                clip_grad_norm_(self.model.rnn_2.parameters(), 10)
                clip_grad_norm_(self.model.rnn_3.parameters(), 10)

            elif str(self.model).startswith('Seq2Seq'):
                output_network = self.model(sentences, sentences_mask, strokes, strokes_mask)
                loss = self.criterion(output_network, sentences, sentences_mask)
                loss.backward()
                # Gradient clipping
                clip_grad_norm_(self.model.parameters(), 10)

            else:
                NotImplementedError("Not a valid model name")

            self.optimizer.step()

            self.writer.set_step((epoch - 1) * self.len_epoch + batch_idx)
            self.train_metrics.update('loss', loss.item())

            if batch_idx % self.log_step == 0:
                self.logger.debug('Train Epoch: {} {} Loss: {:.6f}'.format(
                    epoch,
                    self._progress(batch_idx),
                    loss.item()))

            if batch_idx == self.len_epoch:
                break
        log = self.train_metrics.result()

        if self.do_validation:
            val_log = self._valid_epoch(epoch)
            log.update(**{'val_'+k : v for k, v in val_log.items()})

        if self.lr_scheduler is not None:
            self.lr_scheduler.step()
        return log

    def _valid_epoch(self, epoch):
        """
        Validate after training an epoch

        :param epoch: Integer, current training epoch.
        :return: A log that contains information about validation
        """
        self.model.eval()
        self.valid_metrics.reset()
        with torch.no_grad():
            for batch_idx, (sentences, sentences_mask, strokes, strokes_mask) in enumerate(self.valid_data_loader):

                # Moving input data to device
                sentences, sentences_mask = sentences.to(self.device), sentences_mask.to(self.device)
                strokes, strokes_mask = strokes.to(self.device), strokes_mask.to(self.device)

                # Compute the loss
                if str(self.model).startswith('Unconditional'):
                    output_network = self.model(sentences, sentences_mask, strokes, strokes_mask)
                    gaussian_params = self.model.compute_gaussian_parameters(output_network)
                    loss = self.criterion(gaussian_params, strokes, strokes_mask)

                elif str(self.model).startswith('Conditional'):
                    output_network = self.model(sentences, sentences_mask, strokes, strokes_mask)
                    gaussian_params = self.model.compute_gaussian_parameters(output_network)
                    loss = self.criterion(gaussian_params, strokes, strokes_mask)

                elif str(self.model).startswith('Seq2Seq'):
                    output_network = self.model(sentences, sentences_mask, strokes, strokes_mask)
                    loss = self.criterion(output_network, sentences, sentences_mask)

                else:
                    NotImplementedError("Not a valid model name")

                self.writer.set_step((epoch - 1) * len(self.valid_data_loader) + batch_idx, 'valid')
                self.valid_metrics.update('loss', loss.item())

        # add histogram of model parameters to the tensorboard
        for name, p in self.model.named_parameters():
            self.writer.add_histogram(name, p, bins='auto')
        return self.valid_metrics.result()

    def _progress(self, batch_idx):
        base = '[{}/{} ({:.0f}%)]'
        if hasattr(self.data_loader, 'n_samples'):
            current = batch_idx * self.data_loader.batch_size
            total = self.data_loader.n_samples
        else:
            current = batch_idx
            total = self.len_epoch
        return base.format(current, total, 100.0 * current / total)
