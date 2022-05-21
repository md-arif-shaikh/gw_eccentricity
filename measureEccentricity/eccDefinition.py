"""
Base module to measure eccentricity and mean anomaly for given waveform data.

Part of Defining eccentricity project
Md Arif Shaikh, Mar 29, 2022
"""

import numpy as np
from scipy.interpolate import InterpolatedUnivariateSpline
from .utils import get_peak_via_quadratic_fit, check_kwargs_and_set_defaults
from .plot_settings import use_fancy_plotsettings, colorsDict
import matplotlib.pyplot as plt
import warnings


class eccDefinition:
    """Measure eccentricity from given waveform data dictionary."""

    def __init__(self, dataDict, spline_kwargs=None, extra_kwargs=None):
        """Init eccDefinition class.

        parameters:
        ---------
        dataDict:
            Dictionary containing waveform modes dict, time etc should follow
            the format {"t": time, "hlm": modeDict, ..}, with
            modeDict = {(l, m): hlm_mode_data}.
            For ResidualAmplitude method, also provide "t_zeroecc" and
            "hlm_zeroecc", for the quasicircular counterpart.

        spline_kwargs:
             Arguments to be passed to InterpolatedUnivariateSpline.

        extra_kwargs:
            Any extra kwargs to be passed. Allowed kwargs are
                num_orbits_to_exclude_before_merger:
                    Can be None or a non negative real number.
                    If None, the full waveform data (even post-merger) is used
                    to measure eccentricity, but this might cause issues when
                    interpolating trough extrema.
                    For a non negative real
                    num_orbits_to_exclude_before_merger, that many orbits prior
                    to merger are excluded when finding extrema.
                    Default: 1.
                extrema_finding_kwargs:
                    Dictionary of arguments to be passed to the peak finding
                    function (typically scipy.signal.find_peaks).
                debug:
                    Run additional sanity checks if True.
                    Default: True.
        """
        self.dataDict = dataDict
        self.t = self.dataDict["t"]
        self.hlm = self.dataDict["hlm"]
        self.h22 = self.hlm[(2, 2)]
        self.amp22 = np.abs(self.h22)
        # shift the time axis to make t = 0 at merger
        # t_ref would be then negative. This helps
        # when subtracting quasi circular amplitude from
        # eccentric amplitude in residual amplitude method
        self.t = self.t - get_peak_via_quadratic_fit(
            self.t, self.amp22)[0]
        self.phase22 = - np.unwrap(np.angle(self.h22))
        self.omega22 = np.gradient(self.phase22, self.t)

        if "hlm_zeroecc" in dataDict:
            self.compute_res_amp_and_omega22()

        # Sanity check various kwargs and set default values
        self.spline_kwargs = check_kwargs_and_set_defaults(
            spline_kwargs, self.get_default_spline_kwargs(),
            "spline_kwargs")
        self.extra_kwargs = check_kwargs_and_set_defaults(
            extra_kwargs, self.get_default_extra_kwargs(),
            "extra_kwargs")
        if self.extra_kwargs["num_orbits_to_exclude_before_merger"] \
           is not None and \
           self.extra_kwargs["num_orbits_to_exclude_before_merger"] < 0:
            raise ValueError(
                "num_orbits_to_exclude_before_merger must be non-negative. "
                "Given value was "
                f"{self.extra_kwargs['num_orbits_to_exclude_before_merger']}")

    def get_default_spline_kwargs(self):
        """Defaults for spline settings."""
        default_spline_kwargs = {
            "w": None,
            "bbox": [None, None],
            "k": 3,
            "ext": 2,
            "check_finite": False}
        return default_spline_kwargs

    def get_default_extra_kwargs(self):
        """Defaults for additional kwargs."""
        default_extra_kwargs = {
            "num_orbits_to_exclude_before_merger": 1,
            "extrema_finding_kwargs": {},   # Gets overriden in methods like
                                            # eccDefinitionUsingAmplitude
            "debug": True,
            "omega22_averaging_method": "average_between_extrema"
            }
        return default_extra_kwargs

    def find_extrema(self, extrema_type="maxima"):
        """Find the extrema in the data.

        parameters:
        -----------
        extrema_type:
            One of 'maxima', 'peaks', 'minima' or 'troughs'.

        returns:
        ------
        array of positions of extrema.
        """
        raise NotImplementedError("Please override me.")

    def interp_extrema(self, extrema_type="maxima"):
        """Interpolator through extrema.

        parameters:
        -----------
        extrema_type:
            One of 'maxima', 'peaks', 'minima' or 'troughs'.

        returns:
        ------
        spline through extrema, positions of extrema
        """
        extrema_idx = self.find_extrema(extrema_type)
        # experimenting wih throwing away peaks too close to merger
        # This helps in avoiding unwanted feature in the spline
        # thorugh the extrema
        if self.extra_kwargs["num_orbits_to_exclude_before_merger"] is not None:
            merger_idx = np.argmin(np.abs(self.t))
            phase22_at_merger = self.phase22[merger_idx]
            # one orbit changes the 22 mode phase by 4 pi since
            # omega22 = 2 omega_orb
            phase22_num_orbits_earlier_than_merger = (
                phase22_at_merger
                - 4 * np.pi
                * self.extra_kwargs["num_orbits_to_exclude_before_merger"])
            idx_num_orbit_earlier_than_merger = np.argmin(np.abs(
                self.phase22 - phase22_num_orbits_earlier_than_merger))
            # use only the extrema those are atleast num_orbits away from the
            # merger to avoid unphysical features like nonmonotonic
            # eccentricity near the merger
            extrema_idx = extrema_idx[extrema_idx
                                      <= idx_num_orbit_earlier_than_merger]
        if len(extrema_idx) >= 2:
            spline = InterpolatedUnivariateSpline(self.t[extrema_idx],
                                                  self.omega22[extrema_idx],
                                                  **self.spline_kwargs)
            return spline, extrema_idx
        else:
            raise Exception(
                f"Sufficient number of {extrema_type} are not found."
                " Can not create an interpolator.")

    def measure_ecc(self, tref_in=None, fref_in=None):
        """Measure eccentricity and mean anomaly at reference time.

        parameters:
        ----------
        tref_in:
            Input reference time at which to measure eccentricity and mean
            anomaly.
            Can be a single float or an array.
            NOTE: eccentricity/mean_ano are
            returned on a different time array tref_out, described below.

        fref_in:
            Input reference frequency at which to measure the eccentricity and
            mean anomaly. It can be a single float or an array.
            NOTE: eccentricity/mean anomaly are returned on a different freq
            array fref_out, described below.

            Given an fref_in, we find the corresponding tref_in such that,
            omega22_average(tref_in) = 2 * pi * fref_in.
            Here, omega22_average(t) is a monotonically increasing average
            frequency that is computed from the instantaneous omega22(t).
            Note that this is not a moving average; depending on which averaging
            method is used (see the omega22_averaging_method option below),
            it means slightly different things.

            Currently, following options are implemented to calculate the
            omega22_average
            - "average_between_extrema": Mean of the frequecies given by the
              spline through the peaks and the spline through the troughs.
            - "orbital_average_at_extrema": Orbital average at the extrema
            - "omega22_zeroecc": Frequency of the zero eccentricity waveform
            The default is "average_between_extrema". A method could be passed
            through the "extra_kwargs" option with the key
            "omega22_averaging_method".

        returns:
        --------
        tref_out/fref_out:
            tref_out is the output reference time where eccentricity and mean
            anomaly are measured and fref_out is the output reference frequency
            where eccentricity and mean anomaly are measured.

            NOTE: Only of these is returned depending on whether tref_in or
            fref_in is provided. If tref_in is provided then tref_out is returned
            and if fref_in provided then fref_out is returned.

            tref_out is set as tref_out = tref_in[tref_in >= tmin && tref_in < tmax],
            where tmax = min(t_peaks[-1], t_troughs[-1]),
            and tmin = max(t_peaks[0], t_troughs[0]). This is necessary because
            eccentricity is computed using interpolants of omega22_peaks and
            omega22_troughs. The above cutoffs ensure that we are not
            extrapolating in omega22_peaks/omega22_troughs.
            In addition, if num_orbits_to_exclude_before_merger in extra_kwargs
            is not None, only the data up to that many orbits before merger is
            included when finding the t_peaks/t_troughs. This helps avoid
            unphysical features like nonmonotonic eccentricity near the merger.

            fref_out is set as fref_out = fref_in[fref_in >= fmin && fref_in < fmax].
            where fmin is the frequency at tmin, and fmax is the frequency at tmax.
            tmin/tmax are defined above.

        ecc_ref:
            Measured eccentricity at tref_out/fref_out.

        mean_ano_ref:
            Measured mean anomaly at tref_out/fref_out.
        """
        self.omega22_peaks_interp, self.peaks_location = self.interp_extrema("maxima")
        self.omega22_troughs_interp, self.troughs_location = self.interp_extrema("minima")

        t_peaks = self.t[self.peaks_location]
        t_troughs = self.t[self.troughs_location]
        self.t_max = min(t_peaks[-1], t_troughs[-1])
        self.t_min = max(t_peaks[0], t_troughs[0])
        # check that only one of tref_in or fref_in is provided
        if (tref_in is not None) + (fref_in is not None) != 1:
            raise KeyError("Exactly one of tref_in and fref_in"
                           " should be specified.")
        elif tref_in is not None:
            tref_in = np.atleast_1d(tref_in)
        elif fref_in is not None:
            fref_in = np.atleast_1d(fref_in)
            # get the tref_in from fref_in
            tref_in, self.fref_out = self.compute_tref_in_and_fref_out_from_fref_in(fref_in)
        else:
            raise KeyError("Atleast one of tref_in or fref_in should be"
                           " provided")
        # We measure eccentricity and mean anomaly from t_min to t_max.
        # Note that here we do not include the t_max. This is because
        # the mean anomaly computation requires to looking
        # for a peak before and after the ref time to calculate the current
        # period.
        # If ref time is t_max, which could be equal to the last peak, then
        # there is no next peak and that would cause a problem.
        self.tref_out = tref_in[np.logical_and(tref_in < self.t_max,
                                               tref_in >= self.t_min)]

        # Sanity checks
        # check that fref_out and tref_out is of the same length
        if fref_in is not None:
            if len(self.fref_out) != len(self.tref_out):
                raise Exception(f"Length of fref_out {len(self.fref_out)}"
                                " is different from "
                                f"Length of tref_out {len(self.tref_out)}")
        # Check if tref_out is reasonable
        if len(self.tref_out) == 0:
            if tref_in[-1] > self.t_max:
                raise Exception(f"tref_in is later than t_max={self.t_max}, "
                                "which corresponds to min(last periastron "
                                "time, last apastron time).")
            if tref_in[0] < self.t_min:
                raise Exception(f"tref_in is earlier than t_min={self.t_min}, "
                                "which corresponds to max(first periastron "
                                "time, first apastron time).")
            raise Exception("tref_out is empty. This can happen if the "
                            "waveform has insufficient identifiable "
                            "periastrons/apastrons.")

        # check separation between extrema
        self.orb_phase_diff_at_peaks, \
            self.orb_phase_diff_ratio_at_peaks \
            = self.check_extrema_separation(self.peaks_location, "peaks")
        self.orb_phase_diff_at_troughs, \
            self.orb_phase_diff_ratio_at_troughs \
            = self.check_extrema_separation(self.troughs_location, "troughs")

        # Check if tref_out has a peak before and after.
        # This is required to define mean anomaly.
        # See explaination on why we do not include the last peak above.
        if self.tref_out[0] < t_peaks[0] or self.tref_out[-1] >= t_peaks[-1]:
            raise Exception("Reference time must be within two peaks.")

        # compute eccentricty from the value of omega22_peaks_interp
        # and omega22_troughs_interp at tref_out using the fromula in
        # ref. arXiv:2101.11798 eq. 4
        self.omega22_peak_at_tref_out = self.omega22_peaks_interp(self.tref_out)
        self.omega22_trough_at_tref_out = self.omega22_troughs_interp(self.tref_out)
        self.ecc_ref = ((np.sqrt(self.omega22_peak_at_tref_out)
                         - np.sqrt(self.omega22_trough_at_tref_out))
                        / (np.sqrt(self.omega22_peak_at_tref_out)
                           + np.sqrt(self.omega22_trough_at_tref_out)))

        @np.vectorize
        def compute_mean_ano(time):
            """
            Compute mean anomaly.
            Compute the mean anomaly using Eq.7 of arXiv:2101.11798.
            Mean anomaly grows linearly in time from 0 to 2 pi over
            the range [t_at_last_peak, t_at_next_peak], where t_at_last_peak
            is the time at the previous periastron, and t_at_next_peak is
            the time at the next periastron.
            """
            idx_at_last_peak = np.where(t_peaks <= time)[0][-1]
            t_at_last_peak = t_peaks[idx_at_last_peak]
            t_at_next_peak = t_peaks[idx_at_last_peak + 1]
            t_since_last_peak = time - t_at_last_peak
            current_period = t_at_next_peak - t_at_last_peak
            mean_ano_ref = 2 * np.pi * t_since_last_peak / current_period
            return mean_ano_ref

        # Compute mean anomaly at tref_out
        self.mean_ano_ref = compute_mean_ano(self.tref_out)

        # check if eccenricity is monotonic and convex
        if len(self.tref_out) > 1:
            self.check_monotonicity_and_convexity(
                self.tref_out, self.ecc_ref,
                debug=self.extra_kwargs["debug"])

        if len(self.tref_out) == 1:
            self.mean_ano_ref = self.mean_ano_ref[0]
            self.ecc_ref = self.ecc_ref[0]
            self.tref_out = self.tref_out[0]
        if fref_in is not None and len(self.fref_out) == 1:
            self.fref_out = self.fref_out[0]

        return_array = self.fref_out if fref_in is not None else self.tref_out
        return return_array, self.ecc_ref, self.mean_ano_ref

    def check_extrema_separation(self, extrema_location,
                                 extrema_type="extrema",
                                 max_orb_phase_diff_factor=1.5,
                                 min_orb_phase_diff=np.pi):
        """Check if two extrema are too close or too far."""
        orb_phase_at_extrema = self.phase22[extrema_location] / 2
        orb_phase_diff = np.diff(orb_phase_at_extrema)
        # This might suggest that the data is noisy, for example, and a
        # spurious peak got picked up.
        t_at_extrema = self.t[extrema_location][1:]
        if any(orb_phase_diff < min_orb_phase_diff):
            too_close_idx = np.where(orb_phase_diff < min_orb_phase_diff)[0]
            too_close_times = t_at_extrema[too_close_idx]
            warnings.warn(f"At least a pair of {extrema_type} are too close."
                          " Minimum orbital phase diff is "
                          f"{min(orb_phase_diff)}. Times of occurances are"
                          f" {too_close_times}")
        if any(np.abs(orb_phase_diff - np.pi)
               < np.abs(orb_phase_diff - 2 * np.pi)):
            warnings.warn("Phase shift closer to pi than 2 pi detected.")
        # This might suggest that the peak finding method missed an extrema.
        # We will check if the phase diff at an extrema is greater than
        # max_orb_phase_diff_factor times the orb_phase_diff at the
        # previous peak
        orb_phase_diff_ratio = orb_phase_diff[1:]/orb_phase_diff[:-1]
        # make it of same length as orb_phase_diff by prepending 0
        orb_phase_diff_ratio = np.append([0], orb_phase_diff_ratio)
        if any(orb_phase_diff_ratio > max_orb_phase_diff_factor):
            too_far_idx = np.where(orb_phase_diff_ratio
                                   > max_orb_phase_diff_factor)[0]
            too_far_times = t_at_extrema[too_far_idx]
            warnings.warn(f"At least a pair of {extrema_type} are too far."
                          " Maximum orbital phase diff is "
                          f"{max(orb_phase_diff)}. Times of occurances are"
                          f" {too_far_times}")
        return orb_phase_diff, orb_phase_diff_ratio

    def check_monotonicity_and_convexity(self, tref_out, ecc_ref,
                                         check_convexity=False,
                                         debug=False,
                                         t_for_ecc_test=None):
        """Check if measured eccentricity is a monotonic function of time.

        parameters:
        tref_out:
            Output reference time from eccentricty measurement
        ecc_ref:
            measured eccentricity at tref_out
        check_convexity:
            In addition to monotonicity, it will check for
            convexity as well. Default is False.
        debug:
            If True then warning is generated when length for interpolation
            is greater than 100000. Default is False.
        t_for_ecc_test:
            Time array to build a spline. If None, then uses
            a new time array with delta_t = 0.1 for same range as in tref_out
            Default is None.
        """
        spline = InterpolatedUnivariateSpline(tref_out, ecc_ref)
        if t_for_ecc_test is None:
            t_for_ecc_test = np.arange(tref_out[0], tref_out[-1], 0.1)
            len_t_for_ecc_test = len(t_for_ecc_test)
            if debug and len_t_for_ecc_test > 1e6:
                warnings.warn("time array t_for_ecc_test is too long."
                              f" Length is {len_t_for_ecc_test}")

        # Get derivative of ecc(t) using cubic splines.
        self.decc_dt = spline.derivative(n=1)(t_for_ecc_test)
        self.t_for_ecc_test = t_for_ecc_test
        self.decc_dt = self.decc_dt

        # Is ecc(t) a monotoniccally decreasing function?
        if any(self.decc_dt > 0):
            warnings.warn("Ecc(t) is non monotonic.")

        # Is ecc(t) a convex function? That is, is the second
        # derivative always positive?
        if check_convexity:
            self.d2ecc_dt = spline.derivative(n=2)(t_for_ecc_test)
            self.d2ecc_dt = self.d2ecc_dt
            if any(self.d2ecc_dt > 0):
                warnings.warn("Ecc(t) is concave.")

    def compute_res_amp_and_omega22(self):
        """Compute residual amp22 and omega22."""
        self.hlm_zeroecc = self.dataDict["hlm_zeroecc"]
        self.t_zeroecc = self.dataDict["t_zeroecc"]
        self.h22_zeroecc = self.hlm_zeroecc[(2, 2)]
        self.t_zeroecc = self.t_zeroecc - get_peak_via_quadratic_fit(
            self.t_zeroecc,
            np.abs(self.h22_zeroecc))[0]
        # check that the length of zeroecc waveform is greater than equal
        # to the length of the eccentric waveform.
        # This is important to satisfy. Otherwise there will be extrapolation
        # when we interpolate zeroecc ecc waveform data on the eccentric
        # waveform time.
        if (self.t_zeroecc[0] > self.t[0]):
            raise Exception("Length of zeroecc waveform must be >= the length "
                            "of the eccentric waveform. Eccentric waveform "
                            f"starts at {self.t[0]} whereas zeroecc waveform "
                            f"starts at {self.t_zeroecc[0]}. Try starting the "
                            "zeroecc waveform at lower Momega0.")
        self.amp22_zeroecc_interp = InterpolatedUnivariateSpline(
            self.t_zeroecc, np.abs(self.h22_zeroecc))(self.t)
        self.res_amp22 = self.amp22 - self.amp22_zeroecc_interp

        self.phase22_zeroecc = - np.unwrap(np.angle(self.h22_zeroecc))
        self.omega22_zeroecc = np.gradient(self.phase22_zeroecc,
                                           self.t_zeroecc)
        self.omega22_zeroecc_interp = InterpolatedUnivariateSpline(
            self.t_zeroecc, self.omega22_zeroecc)(self.t)
        self.res_omega22 = (self.omega22
                            - self.omega22_zeroecc_interp)

    def compute_orbital_averaged_omega22_at_extrema(self):
        """Compute reference frequency by orbital averaging at extrema.

        We compute the orbital average of omega22 at the periastrons
        and the apastrons following:
        omega22_avg((t[i]+ t[i+1])/2) = int_t[i]^t[i+1] omega22(t)dt
                                        / (t[i+1] - t[i])
        where t[i] is the time of ith extrema.
        We do this for peaks and troughs and combine the results
        """
        extrema_locations = {"peaks": self.peaks_location,
                             "troughs": self.troughs_location}
        @np.vectorize
        def orbital_averaged_omega22_at_extrema(n, extrema_type="peaks"):
            """Compute orbital averaged omega22 between n and n+1 extrema."""
            # integrate omega22 between n and n+1 extrema
            # We do not need to do the integration here since
            # we already have phase22 available to us which is
            # nothing but the integration of omega22 over time.
            # We want to integrate from nth extrema to n+1 extrema
            # which is equivalent to phase difference between
            # these two extrema
            integ = (self.phase22[extrema_locations[extrema_type][n+1]]
                     - self.phase22[extrema_locations[extrema_type][n]])
            period = (self.t[extrema_locations[extrema_type][n+1]]
                      - self.t[extrema_locations[extrema_type][n]])
            return integ / period
        # get the mid points between the peaks as avg time for peaks
        t_average_peaks = (self.t[self.peaks_location][1:]
                           + self.t[self.peaks_location][:-1]) / 2
        omega22_average_peaks = orbital_averaged_omega22_at_extrema(
            np.arange(len(self.peaks_location) - 1), "peaks")
        # get the mid points between the troughs as avg time for troughs
        t_average_troughs = (self.t[self.troughs_location][1:]
                             + self.t[self.troughs_location][:-1]) / 2
        omega22_average_troughs = orbital_averaged_omega22_at_extrema(
            np.arange(len(self.troughs_location) - 1), "troughs")
        # combine results from avergae at peaks and toughs
        t_average = np.append(t_average_troughs, t_average_peaks)
        # sort the times
        sorted_idx = np.argsort(t_average)
        t_average = t_average[sorted_idx]
        # check if the average omega22 are monotonically increasing
        if any(np.diff(omega22_average_peaks) <= 0):
            raise Exception("Omega22 average at peaks are not strictly "
                            "monotonically increaing")
        if any(np.diff(omega22_average_troughs) <= 0):
            raise Exception("Omega22 average at troughs are not strictly "
                            "monotonically increaing")
        omega22_average = np.append(omega22_average_troughs,
                                    omega22_average_peaks)
        # sort omega22
        omega22_average = omega22_average[sorted_idx]
        return t_average, omega22_average

    def compute_omega22_average_between_extrema(self):
        """Find omega22 average between extrema".

        Take mean of omega22 using spline through omega22 peaks
        and spline through omega22 troughs evaluated at
        the input times.
        """
        t = np.arange(self.t_min, self.t_max, self.t[1] - self.t[0])
        return t, (self.omega22_peaks_interp(t)
                   + self.omega22_troughs_interp(t)) / 2

    def compute_omega22_zeroecc(self):
        """Find omega22 from zeroecc data."""
        t = np.arange(self.t_min, self.t_max, self.t[1] - self.t[0])
        omega22_zeroecc = np.interp(t, self.t_zeroecc, self.omega22_zeroecc)
        return t, omega22_zeroecc

    def get_availabe_omega22_averaging_methods(self):
        """Return available omega22 averaging methods."""
        available_methods = {
            "average_between_extrema": self.compute_omega22_average_between_extrema,
            "orbital_average_at_extrema": self.compute_orbital_averaged_omega22_at_extrema,
            "omega22_zeroecc": self.compute_omega22_zeroecc
        }
        return available_methods

    def compute_tref_in_and_fref_out_from_fref_in(self, fref_in):
        """Compute tref_in and fref_out from fref_in.

        Using chosen omega22 average method we get the tref_in and fref_out
        for the given fref_in.

        When the input is frequencies where eccentricity/mean anomaly is to be
        measured, we internally want to map the input frequencies to a tref_in
        and then we proceed to calculate the eccentricity and mean anomaly for
        these tref_in in the same way as we do when the input array was time
        instead of frequencies.

        First we compute an average frequency to use as our reference frequency
        which depends on what we want our reference frequencies to be. It could
        be
        - Mean of the frequecies given by the spline through the peaks and the
          spline through the troughs, we call this "average_between_extrema"
        - Orbital average at the extrema, we call this "orbital_average_at_extrema"
        - Frequency of the zero eccentricity waveform, called "omega22_zeroecc"

        User can provide a method through the "extra_kwargs" option with the key
        "omega22_averaging_method". Default is "average_between_extrema"

        Once we get the reference frequencies, we create a spline to get time
        as function of these reference frequencies. This should work if the
        refrence frequency is monotonic which it should be.

        Finally we evaluate this spine on the fref_in to get the tref_in.
        """
        available_averaging_methods = self.get_availabe_omega22_averaging_methods()
        method = self.extra_kwargs["omega22_averaging_method"]
        if method in available_averaging_methods:
            self.t_for_omega22_average, self.omega22_average = available_averaging_methods[method]()
            # The fref_in array could have frequencies that is outside the range
            # of frequencies in omega22 average. Therefore, we want to create
            # a separate array of frequencies fref_out which is created by
            # taking on those frequencies that falls within the omega22 average
            # Then proceed to evaluate the tref_in based on these fref_out
            fref_out = self.get_fref_out(fref_in)
            # check if average omega22 is monotonically increasing
            if any(np.diff(self.omega22_average) <= 0):
                warnings.warn(f"Omega22 average from method {method} is not "
                              "monotonically increasing.")
            t_of_fref_out = InterpolatedUnivariateSpline(
                self.omega22_average / (2 * np.pi),
                self.t_for_omega22_average)
            tref_in = t_of_fref_out(fref_out)
            # check if tref_in is monotonically increasing
            if any(np.diff(tref_in) <= 0):
                warnings.warn(f"tref_in from fref_in using method {method} is"
                              " not monotonically increasing.")
            return tref_in, fref_out
        else:
            raise KeyError(f"Omega22 averaging method {method} does not exist."
                           " Must be one of "
                           f"{list(available_averaging_methods.keys())}")

    def get_fref_out(self, fref_in):
        """Get fref_out from fref_in that falls within the valid average omega22 range."""
        fref_out = fref_in[
            np.logical_and(fref_in >= (self.omega22_average[0] / (2 * np.pi)),
                           fref_in < (self.omega22_average[-1]) / (2 * np.pi))]
        if len(fref_out) == 0:
            if fref_in[0] < (self.omega22_average[0] / (2 * np.pi)):
                raise Exception("fref_in is earlier than minimum available "
                                "frequency "
                                f"{self.omega22_average[0]}")
            if fref_in[-1] > (self.omega22_average[-1] / (2 * np.pi)):
                raise Exception("fref_in is later than maximum available "
                                "frequency "
                                f"{self.omega22_average[-1]}")
            else:
                raise Exception("fref_out is empty. This can happen if the "
                                "waveform has insufficient identifiable "
                                "periastrons/apastrons.")
        return fref_out

    def make_diagnostic_plots(self, usetex=True, **kwargs):
        """Make dignostic plots for the eccDefinition method.

        We plot differenct quantities to asses how well our eccentricity
        measurment method is working. This could be seen as a diagnostic tool
        to check an implemented method.

        We plot the following quantities
        - The eccentricity vs vs time
        - decc/dt vs time, this is to test the monotonicity of eccentricity as
          a function of time
        - mean anomaly vs time
        - omega_22 vs time with the peaks and troughs shown. This would show
          if the method is missing any peaks/troughs or selecting one which is
          not a peak/trough
        - deltaPhi_orb(i)/deltaPhi_orb(i-1), where deltaPhi_orb is the
          change in orbital phase from the previous extrema to the ith extrema.
          This helps to look for missing extrema, as there will be a drastic
          (roughly factor of 2) change in deltaPhi_orb(i) if there is a missing
          extrema, and the ratio will go from ~1 to ~2.

        Additionally, we plot the following if data for zero eccentricity is
        provided
        - residual amp22 vs time with the location of peaks and troughs shown.
        - residual omega22 vs time with the location of peaks and troughs
          shown.
        These two plots with further help in understanding any unwanted feature
        in the measured eccentricity vs time plot. For example, non smoothness
        the residual omega22 would indicate that the data in omega22 is not
        good which might be causing glitches in the measured eccentricity plot.
        """
        nrows = 7 if "hlm_zeroecc" in self.dataDict else 5
        figsize = (12, 4 * nrows)
        default_kwargs = {"nrows": nrows,
                          "figsize": figsize}
        for key in default_kwargs:
            if key not in kwargs:
                kwargs.update({key: default_kwargs[key]})
        use_fancy_plotsettings(usetex=usetex)
        fig, ax = plt.subplots(**kwargs)
        self.plot_measured_ecc(fig, ax[0])
        self.plot_decc_dt(fig, ax[1])
        self.plot_mean_ano(fig, ax[2])
        self.plot_extrema_in_omega22(fig, ax[3])
        self.plot_phase_diff_ratio_between_peaks(fig, ax[4])
        if "hlm_zeroecc" in self.dataDict:
            self.plot_residual_omega22(fig, ax[5])
            self.plot_residual_amp(fig, ax[6])
        fig.tight_layout()
        return fig, ax

    def plot_measured_ecc(self, fig=None, ax=None, **kwargs):
        """Plot measured ecc as function of time."""
        if fig is None or ax is None:
            figNew, axNew = plt.subplots()
        else:
            axNew = ax
        default_kwargs = {"c": colorsDict["default"]}
        for key in default_kwargs:
            if key not in kwargs:
                kwargs.update({key: default_kwargs[key]})
        axNew.plot(self.tref_out, self.ecc_ref, **kwargs)
        axNew.set_xlabel("time")
        axNew.set_ylabel("eccentricity")
        axNew.grid()
        if fig is None or ax is None:
            return figNew, axNew
        else:
            return axNew

    def plot_decc_dt(self, fig=None, ax=None, **kwargs):
        """Plot decc_dt as function of time to check monotonicity.

        If decc_dt becomes positive, ecc(t) is not monotonically decreasing.
        """
        if fig is None or ax is None:
            figNew, axNew = plt.subplots()
        else:
            axNew = ax
        default_kwargs = {"c": colorsDict["default"]}
        for key in default_kwargs:
            if key not in kwargs:
                kwargs.update({key: default_kwargs[key]})
        axNew.plot(self.t_for_ecc_test, self.decc_dt, **kwargs)
        axNew.set_xlabel("time")
        axNew.set_ylabel(r"$de/dt$")
        axNew.grid()
        if fig is None or ax is None:
            return figNew, axNew
        else:
            return axNew

    def plot_mean_ano(self, fig=None, ax=None, **kwargs):
        """Plot measured mean anomaly as function of time."""
        if fig is None or ax is None:
            figNew, axNew = plt.subplots()
        else:
            axNew = ax
        axNew.plot(self.tref_out, self.mean_ano_ref,
                   c=colorsDict["default"], **kwargs)
        axNew.set_xlabel("time")
        axNew.set_ylabel("mean anomaly")
        axNew.grid()
        if fig is None or ax is None:
            return figNew, axNew
        else:
            return axNew

    def plot_extrema_in_omega22(self, fig=None, ax=None, **kwargs):
        """Plot omega22, the locations of the apastrons and periastrons, and their corresponding interpolants.

        This would show if the method is missing any peaks/troughs or
        selecting one which is not a peak/trough.
        """
        if fig is None or ax is None:
            figNew, axNew = plt.subplots()
        else:
            axNew = ax
        axNew.plot(self.tref_out, self.omega22_peak_at_tref_out,
                   c=colorsDict["periastron"], label=r"$\omega_{p}(t)$",
                   **kwargs)
        axNew.plot(self.tref_out, self.omega22_trough_at_tref_out,
                   c=colorsDict["apastron"], label=r"$\omega_{a}(t)$",
                   **kwargs)
        # plot only upto merger to make the plot readable
        end = np.argmin(np.abs(self.t))
        axNew.plot(self.t[: end], self.omega22[: end],
                   c=colorsDict["default"], label=r"$\omega_{22}(t)$")
        axNew.plot(self.t[self.peaks_location],
                   self.omega22[self.peaks_location],
                   c=colorsDict["periastron"],
                   marker=".", ls="")
        axNew.plot(self.t[self.troughs_location],
                   self.omega22[self.troughs_location],
                   c=colorsDict["apastron"],
                   marker=".", ls="")
        axNew.set_xlabel("time")
        axNew.grid()
        axNew.set_ylabel(r"$M\omega_{22}(t)$")
        axNew.legend()
        if fig is None or ax is None:
            return figNew, axNew
        else:
            return axNew

    def plot_phase_diff_ratio_between_peaks(self, fig=None, ax=None, **kwargs):
        """Plot phase diff ratio between consecutive as function of time.

        Plots deltaPhi_orb(i)/deltaPhi_orb(i-1), where deltaPhi_orb is the
        change in orbital phase from the previous extrema to the ith extrema.
        This helps to look for missing extrema, as there will be a drastic
        (roughly factor of 2) change in deltaPhi_orb(i) if there is a missing
        extrema, and the ratio will go from ~1 to ~2.
        """
        if fig is None or ax is None:
            figNew, axNew = plt.subplots()
        else:
            axNew = ax
        tpeaks = self.t[self.peaks_location[1:]]
        axNew.plot(tpeaks[1:], self.orb_phase_diff_ratio_at_peaks[1:],
                   c=colorsDict["periastron"],
                   marker=".", label="Periastron phase diff ratio")
        ttroughs = self.t[self.troughs_location[1:]]
        axNew.plot(ttroughs[1:], self.orb_phase_diff_ratio_at_troughs[1:],
                   c=colorsDict["apastron"],
                   marker=".", label="Apastron phase diff ratio")
        axNew.set_xlabel("time")
        axNew.set_ylabel(r"$\Delta \Phi_{orb}[i] / \Delta \Phi_{orb}[i-1]$")
        axNew.grid()
        axNew.legend()
        if fig is None or ax is None:
            return figNew, axNew
        else:
            return axNew

    def plot_residual_omega22(self, fig=None, ax=None, **kwargs):
        """Plot residual omega22, the locations of the apastrons and periastrons, and their corresponding interpolants.

        Useful to look for bad omega22 data near merger.
        We also throw away post merger before since it makes the plot
        unreadble.
        """
        if fig is None or ax is None:
            figNew, axNew = plt.subplots()
        else:
            axNew = ax
        # plot only upto merger to make the plot readable
        end = np.argmin(np.abs(self.t))
        axNew.plot(self.t[: end], self.res_omega2222[:end], c=colorsDict["default"])
        axNew.plot(self.t[self.peaks_location],
                   self.res_omega2222[self.peaks_location],
                   marker=".", ls="", label="Periastron",
                   c=colorsDict["periastron"])
        axNew.plot(self.t[self.troughs_location],
                   self.res_omega2222[self.troughs_location],
                   marker=".", ls="", label="Apastron",
                   c=colorsDict["apastron"])
        axNew.set_xlabel("time")
        axNew.grid()
        axNew.set_ylabel(r"$\Delta\omega_{22}$")
        axNew.legend()
        if fig is None or ax is None:
            return figNew, axNew
        else:
            return axNew

    def plot_residual_amp(self, fig=None, ax=None, **kwargs):
        """Plot residual amp22, the locations of the apastrons and periastrons, and their corresponding interpolants."""
        if fig is None or ax is None:
            figNew, axNew = plt.subplots()
        else:
            axNew = ax
        axNew.plot(self.t, self.res_amp22, c=colorsDict["default"])
        axNew.plot(self.t[self.peaks_location],
                   self.res_amp22[self.peaks_location],
                   c=colorsDict["periastron"],
                   marker=".", ls="", label="Periastron")
        axNew.plot(self.t[self.troughs_location],
                   self.res_amp22[self.troughs_location],
                   c=colorsDict["apastron"],
                   marker=".", ls="", label="Apastron")
        axNew.set_xlabel("time")
        axNew.grid()
        axNew.set_ylabel(r"$\Delta A_{22}$")
        axNew.legend()
        if fig is None or ax is None:
            return figNew, axNew
        else:
            return axNew
