import sys

from audiogen_eval.datasets.load_mel import MelDataset, load_npy_data
from audiogen_eval.metrics.ndb import *
import numpy as np
import argparse
import torch
from torch.utils.data import DataLoader
from audiogen_eval.feature_extractors.melception import Melception
from tqdm import tqdm
from audiogen_eval.metrics import gs

from audiogen_eval import calculate_fid, calculate_isc, calculate_kid, calculate_kl

from audiogen_eval.feature_extractors.panns import Cnn14, Cnn14_16k

import audiogen_eval.audio as Audio


class EvaluationHelper:
    def __init__(self, sampling_rate, device, backbone="cnn14") -> None:

        self.device = device
        self.backbone = backbone
        self.sampling_rate = sampling_rate

        features_list = ["2048", "logits"]
        if self.sampling_rate == 16000:
            self.mel_model = Cnn14(
                features_list=features_list,
                sample_rate=16000,
                window_size=512,
                hop_size=160,
                mel_bins=64,
                fmin=50,
                fmax=8000,
                classes_num=527,
            )
        elif self.sampling_rate == 32000:
            self.mel_model = Cnn14(
                features_list=features_list,
                sample_rate=32000,
                window_size=1024,
                hop_size=320,
                mel_bins=64,
                fmin=50,
                fmax=14000,
                classes_num=527,
            )
        else:
            raise ValueError(
                "We only support the evaluation on 16kHz and 32kHz sampling rate."
            )
        self._stft = None

        self.mel_model.eval()
        self.mel_model.to(self.device)
        self.fbin_mean, self.fbin_std = None, None

    def main(
        self,
        o_filepath,
        resultpath,
        limit_num,
        same_name=False,
        number_of_bins=10,
        evaluation_num=10,
        cache_folder="./results/mnist_toy_example_ndb_cache",
        iter_num=40,
    ):

        # Use the ground truth audio file to calculate mean and std
        # self.calculate_stats_for_normalization(resultpath)

        # gsm = self.getgsmscore(o_filepath, resultpath, iter_num)

        # ndb = self.getndbscore(
        #     o_filepath, resultpath, number_of_bins, evaluation_num, cache_folder
        # )

        metrics = self.calculate_metrics(o_filepath, resultpath, same_name, limit_num)

        # return gsm, ndb, metrics
        return metrics

    def getndbscore(
        self,
        output,
        result,
        number_of_bins=30,
        evaluation_num=50,
        cache_folder="./results/mnist_toy_example_ndb_cache",
    ):
        print("calculating the ndb score:")
        num_workers = 0

        outputloader = DataLoader(
            MelDataset(
                output,
                self._stft,
                self.sampling_rate,
                self.fbin_mean,
                self.fbin_std,
                augment=True,
            ),
            batch_size=1,
            sampler=None,
            num_workers=num_workers,
        )
        resultloader = DataLoader(
            MelDataset(
                result,
                self._stft,
                self.sampling_rate,
                self.fbin_mean,
                self.fbin_std,
            ),
            batch_size=1,
            sampler=None,
            num_workers=num_workers,
        )

        n_query = evaluation_num
        train_samples = load_npy_data(outputloader)

        # print('Initialize NDB bins with training samples')
        mnist_ndb = NDB(
            training_data=train_samples,
            number_of_bins=number_of_bins,
            z_threshold=None,
            whitening=False,
            cache_folder=cache_folder,
        )

        result_samples = load_npy_data(resultloader)
        results = mnist_ndb.evaluate(
            self.sample_from(result_samples, n_query), "generated result"
        )
        plt.figure()
        mnist_ndb.plot_results()

    def getgsmscore(self, output, result, iter_num=40):
        num_workers = 0

        print("calculating the gsm score:")

        outputloader = DataLoader(
            MelDataset(
                output,
                self._stft,
                self.sampling_rate,
                self.fbin_mean,
                self.fbin_std,
                augment=True,
            ),
            batch_size=1,
            sampler=None,
            num_workers=num_workers,
        )
        resultloader = DataLoader(
            MelDataset(
                result,
                self._stft,
                self.sampling_rate,
                self.fbin_mean,
                self.fbin_std,
            ),
            batch_size=1,
            sampler=None,
            num_workers=num_workers,
        )

        x_train = load_npy_data(outputloader)

        x_1 = x_train
        newshape = int(x_1.shape[1] / 8)
        x_1 = np.reshape(x_1, (-1, newshape))
        rlts = gs.rlts(x_1, gamma=1.0 / 128, n=iter_num)
        mrlt = np.mean(rlts, axis=0)

        gs.fancy_plot(mrlt, label="MRLT of data_1", color="C0")
        plt.xlim([0, 30])
        plt.legend()

        x_train = load_npy_data(resultloader)

        x_1 = x_train
        x_1 = np.reshape(x_1, (-1, newshape))
        rlts = gs.rlts(x_1, gamma=1.0 / 128, n=iter_num)

        mrlt = np.mean(rlts, axis=0)

        gs.fancy_plot(mrlt, label="MRLT of data_2", color="orange")
        plt.xlim([0, 30])
        plt.legend()
        plt.show()

    def calculate_metrics(self, output, result, same_name, limit_num=None):
        torch.manual_seed(0)
        num_workers = 0

        outputloader = DataLoader(
            MelDataset(
                output,
                self._stft,
                self.sampling_rate,
                self.fbin_mean,
                self.fbin_std,
                augment=True,
                limit_num=limit_num,
            ),
            batch_size=1,
            sampler=None,
            num_workers=num_workers,
        )
        resultloader = DataLoader(
            MelDataset(
                result,
                self._stft,
                self.sampling_rate,
                self.fbin_mean,
                self.fbin_std,
                limit_num=limit_num,
            ),
            batch_size=1,
            sampler=None,
            num_workers=num_workers,
        )

        out = {}

        print("Extracting features from input_1")
        featuresdict_1 = self.get_featuresdict(outputloader)
        print("Extracting features from input_2")
        featuresdict_2 = self.get_featuresdict(resultloader)

        # if cfg.have_kl:
        metric_kl = calculate_kl(featuresdict_1, featuresdict_2, "logits", same_name)
        out.update(metric_kl)
        # if cfg.have_isc:
        metric_isc = calculate_isc(
            featuresdict_1,
            feat_layer_name="logits",
            splits=4,
            samples_shuffle=True,
            rng_seed=2020,
        )
        out.update(metric_isc)
        # if cfg.have_fid:
        metric_fid = calculate_fid(
            featuresdict_1, featuresdict_2, feat_layer_name="2048"
        )
        out.update(metric_fid)
        # if cfg.have_kid:
        metric_kid = calculate_kid(
            featuresdict_1,
            featuresdict_2,
            feat_layer_name="2048",
            subsets=100,
            subset_size=1000,
            degree=3,
            gamma=None,
            coef0=1,
            rng_seed=2020,
        )
        out.update(metric_kid)

        print("\n".join((f"{k}: {v:.7f}" for k, v in out.items())))
        print("\n")
        print(limit_num)
        print(
            f'KL: {out.get("kullback_leibler_divergence", float("nan")):8.5f};',
            f'ISc: {out.get("inception_score_mean", float("nan")):8.5f} ({out.get("inception_score_std", float("nan")):5f});',
            f'FID: {out.get("frechet_inception_distance", float("nan")):8.5f};',
            f'KID: {out.get("kernel_inception_distance_mean", float("nan")):.5f}',
            f'({out.get("kernel_inception_distance_std", float("nan")):.5f})',
        )
        result = {
            "kullback_leibler_divergence": out.get(
                "kullback_leibler_divergence", float("nan")
            ),
            "inception_score_mean": out.get("inception_score_mean", float("nan")),
            "inception_score_std": out.get("inception_score_std", float("nan")),
            "frechet_inception_distance": out.get(
                "frechet_inception_distance", float("nan")
            ),
            "kernel_inception_distance_mean": out.get(
                "kernel_inception_distance_mean", float("nan")
            ),
            "kernel_inception_distance_std": out.get(
                "kernel_inception_distance_std", float("nan")
            ),
        }
        return result

    def get_featuresdict(self, dataloader):

        out = None
        out_meta = None

        # transforms=StandardNormalizeAudio()

        for waveform, filename in tqdm(dataloader):
            metadict = {
                "file_path_": filename,
            }
            waveform = waveform.squeeze(1)

            # batch = transforms(batch)
            waveform = waveform.float().to(self.device)

            with torch.no_grad():
                featuresdict = self.mel_model(waveform)

            # featuresdict = self.mel_model.convert_features_tuple_to_dict(features)
            featuresdict = {k: [v.cpu()] for k, v in featuresdict.items()}

            if out is None:
                out = featuresdict
            else:
                out = {k: out[k] + featuresdict[k] for k in out.keys()}

            if out_meta is None:
                out_meta = metadict
            else:
                out_meta = {k: out_meta[k] + metadict[k] for k in out_meta.keys()}

        out = {k: torch.cat(v, dim=0) for k, v in out.items()}
        return {**out, **out_meta}

    def sample_from(self, samples, number_to_use):
        assert samples.shape[0] >= number_to_use
        rand_order = np.random.permutation(samples.shape[0])
        return samples[rand_order[: samples.shape[0]], :]
