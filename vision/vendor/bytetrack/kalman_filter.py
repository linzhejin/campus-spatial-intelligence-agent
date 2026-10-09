"""Bounding-box Kalman filter adapted from FoundationVision/ByteTrack (MIT)."""
import numpy as np
from scipy.linalg import cho_factor, cho_solve


class KalmanFilter:
    def __init__(self):
        ndim, dt = 4, 1.0
        self._motion_mat = np.eye(2 * ndim, 2 * ndim)
        for index in range(ndim):
            self._motion_mat[index, ndim + index] = dt
        self._update_mat = np.eye(ndim, 2 * ndim)
        self._std_weight_position = 1.0 / 20
        self._std_weight_velocity = 1.0 / 160

    def initiate(self, measurement):
        mean_pos = np.asarray(measurement, dtype=np.float64)
        mean = np.r_[mean_pos, np.zeros_like(mean_pos)]
        height = mean_pos[3]
        std = [2 * self._std_weight_position * height,
               2 * self._std_weight_position * height, 1e-2,
               2 * self._std_weight_position * height,
               10 * self._std_weight_velocity * height,
               10 * self._std_weight_velocity * height, 1e-5,
               10 * self._std_weight_velocity * height]
        return mean, np.diag(np.square(std))

    def predict(self, mean, covariance):
        height = mean[3]
        std_pos = [self._std_weight_position * height,
                   self._std_weight_position * height, 1e-2,
                   self._std_weight_position * height]
        std_vel = [self._std_weight_velocity * height,
                   self._std_weight_velocity * height, 1e-5,
                   self._std_weight_velocity * height]
        motion_cov = np.diag(np.square(np.r_[std_pos, std_vel]))
        mean = self._motion_mat @ mean
        covariance = self._motion_mat @ covariance @ self._motion_mat.T + motion_cov
        return mean, covariance

    def multi_predict(self, mean, covariance):
        if len(mean) == 0:
            return mean, covariance
        heights = mean[:, 3]
        std_pos = [self._std_weight_position * heights,
                   self._std_weight_position * heights,
                   1e-2 * np.ones_like(heights),
                   self._std_weight_position * heights]
        std_vel = [self._std_weight_velocity * heights,
                   self._std_weight_velocity * heights,
                   1e-5 * np.ones_like(heights),
                   self._std_weight_velocity * heights]
        motion_cov = np.asarray([
            np.diag(np.square(np.asarray([value[index] for value in std_pos + std_vel])))
            for index in range(len(mean))
        ])
        mean = mean @ self._motion_mat.T
        left = np.dot(self._motion_mat, covariance).transpose((1, 0, 2))
        covariance = np.dot(left, self._motion_mat.T) + motion_cov
        return mean, covariance

    def project(self, mean, covariance):
        height = mean[3]
        std = [self._std_weight_position * height,
               self._std_weight_position * height, 1e-1,
               self._std_weight_position * height]
        projected_mean = self._update_mat @ mean
        projected_cov = self._update_mat @ covariance @ self._update_mat.T
        return projected_mean, projected_cov + np.diag(np.square(std))

    def update(self, mean, covariance, measurement):
        projected_mean, projected_cov = self.project(mean, covariance)
        cross_cov = covariance @ self._update_mat.T
        factor = cho_factor(projected_cov, lower=True, check_finite=False)
        kalman_gain = cho_solve(
            factor, np.dot(covariance, self._update_mat.T).T,
            check_finite=False,
        ).T
        innovation = np.asarray(measurement, dtype=np.float64) - projected_mean
        new_mean = mean + kalman_gain @ innovation
        new_covariance = covariance - kalman_gain @ projected_cov @ kalman_gain.T
        return new_mean, new_covariance
