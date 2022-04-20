import librosa
import numpy as np
import scipy.io as sio
import scipy.signal
from sklearn.preprocessing import scale


class DataExtractor:
    @staticmethod
    def read_physionet_mat(file_path):
        mat = sio.loadmat(file_path)  # load mat-file
        mdata = mat['example_data']  # variable in mat file
        ndata = {n: mdata[n][0, 0] for n in mdata.dtype.names}
        pcg_recordings = ndata['example_audio_data'].squeeze()
        patient_ids = ndata['patient_number'].squeeze()
        return pcg_recordings, patient_ids

    @staticmethod
    def resample_signal(data, original_rate=1000, new_rate=50):
        resampled_data = []
        for recording in data:
            time_secs = len(recording) / original_rate
            number_of_samples = int(time_secs * new_rate)
            # downsample from the filtered signal
            resampled_data.append(scipy.signal.resample(recording, number_of_samples).squeeze())
        return np.array(resampled_data)

    @staticmethod
    def get_power_spectrum(data, sampling_rate, window_length, window_overlap, window_type='hann'):
        psd_data = np.zeros(data.shape, dtype=object)
        for i in range(len(data)):
            recording = data[i]
            # Apply high-pass and low pass order 2 Butterworth filters with respective 25 and 400 Hz cut-offs
            sos_hp = scipy.signal.butter(N=2, Wn=25, btype='highpass', analog=False, fs=sampling_rate, output='sos')
            sos_lp = scipy.signal.butter(N=2, Wn=400, btype='lowpass', analog=False, fs=sampling_rate, output='sos')
            filtered = scipy.signal.sosfilt(sos_hp, recording)
            filtered = scipy.signal.sosfilt(sos_lp, filtered)
            _, _, psd = scipy.signal.stft(filtered.squeeze(),
                                          fs=sampling_rate,
                                          window=window_type,
                                          nperseg=window_length,
                                          noverlap=window_overlap)
            # transform the signal from complex to real-valued
            # Transpose to get the number of windows in first dimension to have the frequencies has a fixed
            # dimension for the CNNs
            psd = np.abs(psd).T

            # PSD_norm(t,f) = PSD(t,f)/ A; A=sum_t=0^T-1 sum_f=0^F-1 PSD(t,f)/T => sum PSD_Norm(t,f) = T
            length_psd = psd.shape[0]
            normalization = np.sum(np.sum(psd, axis=0))
            psd_data[i] = psd / (normalization / length_psd)

        return psd_data

    @staticmethod
    def get_mfccs(data, sampling_rate, window_length, window_overlap, n_mfcc, fmin=25, fmax=400, resample=None,
                  delta=True, delta_delta=True, delta_diff=2):
        if resample is not None:
            data = DataExtractor.resample_signal(data, new_rate=resample)
        mfcc_data = np.zeros(data.shape, dtype=object)
        _hop_length = window_length - window_overlap
        for i in range(len(data)):
            recording = data[i]
            mfcc = librosa.feature.mfcc(y=recording.squeeze(),
                                        n_fft=window_length,
                                        sr=sampling_rate,
                                        hop_length=_hop_length,
                                        n_mfcc=n_mfcc,
                                        fmin=fmin,
                                        fmax=fmax)
            mfcc = mfcc.T  # switch the time domain to the first dimension
            length_mfcc = mfcc.shape[0]
            normalization = np.sum(np.sum(mfcc, axis=0))
            mfcc = mfcc / (normalization / length_mfcc)

            # Kind of ugly but it works. If delta-delta is True then concatenates delta also.
            if delta_delta is True:
                delta_ = DataExtractor.calculate_delta(mfcc, delta_diff)
                delta_delta_ = DataExtractor.calculate_delta(delta_, delta_diff)
                mfcc_data[i] = np.concatenate([mfcc, delta_, delta_delta_], axis=1)
            elif delta is True:
                delta_ = DataExtractor.calculate_delta(mfcc, delta_diff)
                mfcc_data[i] = np.concatenate([mfcc, delta_], axis=1)
            else:
                mfcc_data[i] = mfcc

        return mfcc_data

    @staticmethod
    def calculate_delta(coefficients, delta_diff=2):
        """
        Given coeffients of a delta^k mfcc, calculates delta^(k+1).
        Normalization according to:
        http://practicalcryptography.com/miscellaneous/machine-learning/guide-mel-frequency-cepstral-coefficients-mfccs/#deltas-and-delta-deltas
        Parameters
        ----------
        coefficients : np.ndarray
            A ndarray of shape (t, c), i.e. c coefficients for every sample t in the signal
        delta_diff : int
            The offset in time used to calculate the deltas

        Returns
        -------
        np.ndarray
            A ndarray of shape (t, c), where each t has c delta coefficients
        """
        delta = np.zeros(coefficients.shape)
        norm = 2 * np.sum(np.arange(1, delta_diff + 1) ** 2)
        for t in range(coefficients.shape[0]):
            d_t = 0
            for n in range(delta_diff):
                d_t += (n + 1) * (coefficients[min(coefficients.shape[0] - 1, t + n), :] - coefficients[max(0, t - n), :])
            delta[t, :] = d_t / norm
        return delta

    @staticmethod
    def extract(path, patch_size, filter_noisy=True):
        data = sio.loadmat(path, squeeze_me=True)
        raw_features = data['Feat_cell']
        raw_labels = data['Lab_cell']
        raw_patient_ids = data['Number_cell']

        # remove sounds shorter than patch size (and record sound indexes)
        length_sounds = np.array([len(raw_features[j]) for j in range(len(raw_features))])
        valid_indices = np.array([j for j in range(len(raw_features)) if len(raw_features[j]) >= patch_size])
        # Filter noisy labels. (Use for filtering out small label mistakes in Springer16)
        if filter_noisy:
            labels_ = raw_labels[valid_indices]
            noisy_indices = []
            for idx, lab in enumerate(labels_):
                lab = lab - 1
                for t in range(1, len(lab)):
                    if lab[t] != lab[t - 1] and lab[t] != (lab[t - 1] + 1) % 4:
                        noisy_indices.append(valid_indices[idx])

            valid_indices = np.array(list(set(valid_indices) - set(noisy_indices)))
            print(f"Filtered {len(set(noisy_indices))} observations containing noisy labels")
        features = raw_features[valid_indices]
        labels = raw_labels[valid_indices]
        patient_ids = raw_patient_ids[valid_indices]

        return valid_indices, features, labels, patient_ids, length_sounds

    @staticmethod
    def filter_by_index(processed_features, indices):
        return processed_features[indices]
