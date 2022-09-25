"""Utility to load waveform data from lvcnr files or LAL."""
import numpy as np
from .utils import peak_time_via_quadratic_fit
from .utils import amplitude_using_all_modes
from .utils import check_kwargs_and_set_defaults
import h5py
import lal
import lalsimulation as lalsim
import warnings
from .utils import interpolate


def get_available_waveform_origins():
    """Get available origins of waveforms that could be loaded."""
    return {
        "LAL": load_LAL_waveform,
        "LVCNR": load_lvcnr_waveform,
        "EOB": load_EOB_waveform,
        "EMRI": load_EMRI_waveform}


def load_waveform(origin="LAL", **kwargs):
    """Load waveform.

    parameters:
    ----------
    origin: str
        The origin of the waveform to be provided.  This can be one of
        - "LAL": Compute waveform by a call to the LAL-library.
        - "LVCNR": Import waveform by reading a file in the LVCNR-data format.
        - "EOB": Import EOB waveform generated using SEOBNRv4EHM
            (arxiv:2112.06952).
        - "EMRI": Import EMRI waveform generated by Maarten.
        In each case, the `kwargs` dictionary provides the needed information
        to uniquely specify the waveform.
    kwargs:
        Kwargs dictionary to be passed to the waveform loading functions.
        As mentioned above, the dictionary would depend on the `origin`
        of the waveform to be loaded/imported/generated.
        - "LAL": For generating waveform calling the LAL library. See
            load_data.load_LAL_waveform for the allowed kwargs and defaults.
        - "LVCNR": For importing NR waveform in LVCNR format the function
            load_data.load_lvcnr_waveform is called. Look for the allowed
            and default list of kwargs there.
        - "EOB": For importing EOB waveforms generated using SEOBNRv4EHM.
            See load_data.load_EOB_EccTest_file for allowed kwargs.
        - "EMRI": For import EMRI waveforms generated by Maarten.
            See load_data.load_EMRI_waveform for allowed kwargs.
    Returns:
    --------
    dataDict:
        Dictionary of time, modes etc. For detailed structure of the returned
        dataDict see gw_eccentricity.measure_eccentricity.
    """
    available_origins = get_available_waveform_origins()
    if origin in available_origins:
        return available_origins[origin](**kwargs)
    else:
        raise Exception(f"Unknown origin {origin}. "
                        f"Should be one of {list(available_origins.keys())}")


def load_LAL_waveform(**kwargs):
    """Load waveforms calling the LAL Library.

    The kwargs could be the following:
    approximant: str
        Name of the waveform model to be used for generating the waveform.
        default is "EccentricTD".
    q: float
        Mass ratio of the system.
        default is 1.
    chi1: 1d array of size 3
        3-element 1d array of spin components of the 1st Black hole.
        default is [0.0, 0.0, 0.0].
    chi2: 1d array of size 3
        3-element 1d array of spin components of the 1st Black hole.
        default is [0.0, 0.0, 0.0].
    ecc: float
        Initial eccentricity of the binary at Momega0 (see below).
        default is 1e-5.
    mean_ano: float
        Initial Mean anomaly of the bianry at Momega0 (see below).
        default is 0.0.
    Momega0: float
        Starting orbital frequency in dimensionless units.
        default is 0.01.
    deltaTOverM: float
        Time steps in dimensionless units. default is 0.1.
    physicalUnits: bool
        If True returns modes in MKS units.
        Default is False.
    include_zero_ecc: bool
        If True, quasicircular waveform is created and
        returned. The quasicircular waveform is generated using the
        same set of parameters except eccentricity set to zero.
        In some cases, e=0 is not supported and we set it small value
        like e=1e-5.
        Default is False.
    """
    default_lal_kwargs = {
        "approximant": "EccentricTD",
        "q": 1.0,
        "chi1": [0.0, 0.0, 0.0],
        "chi2": [0.0, 0.0, 0.0],
        "ecc": 1e-5,
        "mean_ano": 0.0,
        "Momega0": 0.01,
        "deltaTOverM": 0.1,
        "physicalUnits": False,
        "include_zero_ecc": False
    }

    # check and set default kwargs
    check_kwargs_and_set_defaults(kwargs, default_lal_kwargs, "LAL Kwarsgs",
                                  "load_data.load_LAL_waveform")
    # FIXME, this assumes single mode models, talk to Vijay about
    # how to handle other models.
    dataDict = load_LAL_waveform_using_hack(
        kwargs['approximant'],
        kwargs['q'],
        kwargs['chi1'],
        kwargs['chi2'],
        kwargs['ecc'],
        kwargs['mean_ano'],
        kwargs['Momega0'],
        kwargs['deltaTOverM'],
        kwargs['physicalUnits'])

    if kwargs['include_zero_ecc']:
        # Keep all other params fixed but set ecc=0.
        zero_ecc_kwargs = kwargs.copy()
        # FIXME: Stupid EccentricTD only works for finite ecc
        if kwargs["approximant"] == "EccentricTD":
            zero_ecc_kwargs['ecc'] = 1e-5
        else:
            zero_ecc_kwargs['ecc'] = 0
        zero_ecc_kwargs['include_zero_ecc'] = False   # to avoid infinite loops
        dataDict_zero_ecc = load_waveform(**zero_ecc_kwargs)
        t_zeroecc = dataDict_zero_ecc['t']
        hlm_zeroecc = dataDict_zero_ecc['hlm']
        dataDict.update({'t_zeroecc': t_zeroecc,
                         'hlm_zeroecc': hlm_zeroecc})
    return dataDict


def load_LAL_waveform_using_hack(approximant, q, chi1, chi2, ecc, mean_ano,
                                 Momega0, deltaTOverM, physicalUnits=False):
    """Load LAL waveforms."""
    # Many LAL models don't return the modes. So, to get h22 we evaluate the
    # strain at (incl, phi)=(0,0) and divide by Ylm(0,0).  NOTE: This only
    # works if the only mode is the (2,2) mode.
    phi_ref = 0
    inclination = 0

    # h = hp -1j * hc
    t, h = generate_LAL_waveform(approximant, q, chi1, chi2,
                                 deltaTOverM, Momega0, eccentricity=ecc,
                                 phi_ref=phi_ref, inclination=inclination,
                                 physicalUnits=physicalUnits)

    Ylm = lal.SpinWeightedSphericalHarmonic(inclination, phi_ref, -2, 2, 2)
    mode_dict = {(2, 2): h/Ylm}
    # Make t = 0 at the merger. This would help when getting
    # residual amplitude by subtracting quasi-circular counterpart
    t = t - peak_time_via_quadratic_fit(
        t,
        amplitude_using_all_modes(mode_dict))[0]

    dataDict = {"t": t, "hlm": mode_dict}
    return dataDict


def generate_LAL_waveform(approximant, q, chi1, chi2, deltaTOverM, Momega0,
                          inclination=0, phi_ref=0., longAscNodes=0,
                          eccentricity=0, meanPerAno=0,
                          alignedSpin=True, lambda1=None, lambda2=None,
                          physicalUnits=False):
    """Generate waveform for a given approximant using LALSuite.

    Returns dimless time and dimless complex strain.
    parameters:
    ----------
    approximant: str
        Name of approximant.
    q: float
        Mass ratio q>=1.
    chi1: array/list of len=3
        Dimensionless spin vector of larger BH.
    chi2: array/list of len=3
        Dimensionless spin vector of smaller BH.
    deltaTOverM: float
        Dimensionless time step size.
    Momega0: float
        Dimensionless starting orbital frequency for waveform (rad/s).
    inclination: float
        Inclination angle in radians.
    phi_ref: float
        Lalsim stuff.
    longAscNodes: float
        Longiture of Ascending nodes.
    eccentricity: float
        Eccentricity.
    meanPerAno: float
        Mean anomaly of periastron.
    alignedSpin:
        Assume aligned spin approximant.
    lambda1:
        Tidal parameter for larger BH.
    lambda2:
        Tidal parameter for smaller BH.
    physicalUnits:
        If True, return in physical units.

    return:
    t: array
        Dimensionless time.
    h: complex array
        Dimensionless complex strain h_{+} -i*h_{x}.
    """
    chi1 = np.array(chi1)
    chi2 = np.array(chi2)

    if alignedSpin:
        if np.sum(np.sqrt(chi1[:2]**2)) > 1e-5 or np.sum(
                np.sqrt(chi2[:2]**2)) > 1e-5:
            raise Exception("Got precessing spins for aligned spin "
                            "approximant.")
        if np.sum(np.sqrt(chi1[:2]**2)) != 0:
            chi1[:2] = 0
        if np.sum(np.sqrt(chi2[:2]**2)) != 0:
            chi2[:2] = 0

    # sanity checks
    if np.sqrt(np.sum(chi1**2)) > 1:
        raise Exception('chi1 out of range.')
    if np.sqrt(np.sum(chi2**2)) > 1:
        raise Exception('chi2 out of range.')
    if len(chi1) != 3:
        raise Exception('chi1 must have size 3.')
    if len(chi2) != 3:
        raise Exception('chi2 must have size 3.')

    # use M=10 and distance=1 Mpc, but will scale these out before outputting h
    M = 10      # dimless mass
    distance = 1.0e6 * lal.PC_SI

    approxTag = lalsim.GetApproximantFromString(approximant)
    MT = M * lal.MTSUN_SI
    f_low = Momega0/np.pi/MT
    f_ref = f_low

    # component masses of the binary
    m1_kg = M * lal.MSUN_SI * q / (1. + q)
    m2_kg = M * lal.MSUN_SI / (1. + q)

    # tidal parameters if given
    if lambda1 is not None or lambda2 is not None:
        dictParams = lal.CreateDict()
        lalsim.SimInspiralWaveformParamsInsertTidalLambda1(dictParams, lambda1)
        lalsim.SimInspiralWaveformParamsInsertTidalLambda2(dictParams, lambda2)
    else:
        dictParams = None

    hp, hc = lalsim.SimInspiralChooseTDWaveform(
        m1_kg, m2_kg, chi1[0], chi1[1], chi1[2], chi2[0], chi2[1], chi2[2],
        distance, inclination, phi_ref,
        longAscNodes, eccentricity, meanPerAno,
        deltaTOverM*MT, f_low, f_ref, dictParams, approxTag)

    h = np.array(hp.data.data - 1.j*hc.data.data)
    t = deltaTOverM * MT * np.arange(len(h)) if physicalUnits else (
        deltaTOverM * np.arange(len(h)))

    return t, h if physicalUnits else h * distance/MT/lal.C_SI


def time_dimless_to_mks(M):
    """Factor to convert time from dimensionless units to SI units.

    parameters
    ----------
    M:
        Mass of system in the units of solar mass.

    Returns
    -------
    converting factor
    """
    return M * lal.MTSUN_SI


def amplitude_dimless_to_mks(M, D):
    """Factor to rescale amp from dimensionless units to SI units.

    parameters
    ----------
    M:
        Mass of the system in units of solar mass.
    D:
        Luminosity distance in units of megaparsecs.

    Returns
    -------
    Scaling factor
    """
    return lal.G_SI * M * lal.MSUN_SI / (lal.C_SI**2 * D * 1e6 * lal.PC_SI)


def load_lvcnr_waveform(**kwargs):
    """Load modes from lvcnr files.

    parameters:
    ----------
    kwargs: Could be the followings
    filepath: str
        Path to lvcnr file.

    deltaTOverM: float
        Time step. Default is 0.1

    Momega0: float
        Lower frequency to start waveform generation. Default is 0.
        If Momega0 = 0, uses the entire NR data. The actual Momega0 will be
        returned.

    include_zero_ecc: bool
        If True returns PhenomT waveform mode for same set of parameters
        except eccentricity set to zero. Default is True.

    num_orbits_to_remove_as_junk: float
        Number of orbits to throw away as junk from the begining of the NR
        data. Default is 2.

    returns:
    -------
        Dictionary of modes dict, parameter dict and also zero ecc mode dict if
        include_zero_ecc is True.

    t:
        Time array.
    hlm:
        Dictionary of modes.
    params_dict:
        Dictionary of parameters.
    Optionally,
    t_zeroecc:
        Time array for zero ecc modes
    hlm_zeroecc:
        Mode dictionary for zero eccentricity
    """
    default_kwargs = {
        "filepath": None,
        "deltaTOverM": 0.1,
        "Momega0": 0,  # 0 means that the full NR waveform is returned
        "include_zero_ecc": True,
        "num_orbits_to_remove_as_junk": 2}

    kwargs = check_kwargs_and_set_defaults(kwargs, default_kwargs,
                                           "lvcnr kwargs")
    filepath = kwargs["filepath"]
    M = 10  # will be factored out
    dt = kwargs["deltaTOverM"] * time_dimless_to_mks(M)
    dist_mpc = 1  # will be factored out
    f_low = kwargs["Momega0"] / np.pi / time_dimless_to_mks(M)

    NRh5File = h5py.File(filepath, "r")
    params_NR = lal.CreateDict()
    lalsim.SimInspiralWaveformParamsInsertNumRelData(params_NR, filepath)

    # Metadata parameters masses:
    m1 = NRh5File.attrs["mass1"]
    m2 = NRh5File.attrs["mass2"]
    m1SI = m1 * M / (m1 + m2) * lal.MSUN_SI
    m2SI = m2 * M / (m1 + m2) * lal.MSUN_SI

    distance = dist_mpc * 1.0e6 * lal.PC_SI
    # If f_low == 0, update it to the start frequency so that
    # we get the right start frequency
    if f_low == 0:
        f_low = NRh5File.attrs["f_lower_at_1MSUN"] / M
    f_ref = 0  # Non zero f_ref is not supported since the lvcnr format of the
    # files we are testing is format 1.
    spins = lalsim.SimInspiralNRWaveformGetSpinsFromHDF5File(f_ref, M,
                                                             filepath)
    s1x = spins[0]
    s1y = spins[1]
    s1z = spins[2]
    s2x = spins[3]
    s2y = spins[4]
    s2z = spins[5]

    # Generating the NR modes
    values_mode_array = lalsim.SimInspiralWaveformParamsLookupModeArray(
        params_NR)
    _, modes = lalsim.SimInspiralNRWaveformGetHlms(
        dt,
        m1SI,
        m2SI,
        distance,
        f_low,
        f_ref,
        s1x,
        s1y,
        s1z,
        s2x,
        s2y,
        s2z,
        filepath,
        values_mode_array)

    modes_dict = {}
    while modes is not None:
        modes_dict[(modes.l, modes.m)] = (
            modes.mode.data.data
            / amplitude_dimless_to_mks(M, dist_mpc))
        modes = modes.next

    t = np.arange(len(modes_dict[(2, 2)])) * dt
    t = t / time_dimless_to_mks(M)
    # shift the times to make merger a t = 0
    t = t - peak_time_via_quadratic_fit(
        t,
        amplitude_using_all_modes(modes_dict))[0]

    q = m1SI/m2SI
    try:
        eccentricity = float(NRh5File.attrs["eccentricity"])
    except ValueError:
        eccentricity = None

    NRh5File.close()

    # remove junk from the begining of the data
    t, modes_dict = reomve_junk_from_nr_data(
        t,
        modes_dict,
        kwargs["num_orbits_to_remove_as_junk"])

    return_dict = {"t": t,
                   "hlm": modes_dict}

    params_dict = {"q": q,
                   "chi1": [s1x, s1y, s1z],
                   "chi2": [s2x, s2y, s2z],
                   "ecc": eccentricity,
                   "mean_ano": 0.0,
                   "deltaTOverM": t[1] - t[0],
                   "Momega0": (
                       f_low
                       * np.pi
                       * time_dimless_to_mks(M)),
                   }
    return_dict.update({"params_dict": params_dict})

    if ("include_zero_ecc" in kwargs) and kwargs["include_zero_ecc"]:
        dataDict_zeroecc = get_zeroecc_dataDict_for_lvcnr(return_dict)
        return_dict.update(dataDict_zeroecc)
    return return_dict


def get_zeroecc_dataDict_for_lvcnr(nr_dataDict):
    """Get the zero ecc data dict corresponding to a nr data.

    Params:
    -------
    nr_dataDict:
        Data Dictionary containing NR data including params_dict.
    Returns:
    -------
    dataDict_zeroecc:
        Data Dictionary containing zero ecc data.
    """
    # Keep all other params fixed but set ecc = 0 and generate IMRPhenomT
    # waveform
    zero_ecc_kwargs = nr_dataDict["params_dict"].copy()
    zero_ecc_kwargs["ecc"] = 0.0
    zero_ecc_kwargs["approximant"] = "IMRPhenomT"
    zero_ecc_kwargs["include_zero_ecc"] = False  # to avoid double calc
    # calculate the Momega0 so that the length is >= the length of the NR
    # waveform.
    # First we compute the inspiral time of the NR waveform.
    # get time at merger of the NR waveform
    t_merger = peak_time_via_quadratic_fit(
            nr_dataDict["t"],
            amplitude_using_all_modes(nr_dataDict["hlm"]))[0]
    M = 10  # will be factored out
    inspiralTime = (t_merger
                    - nr_dataDict["t"][0]) * time_dimless_to_mks(M)
    # get the initial frequency to generate waveform of inspiral time
    # roughly equal to that of the NR one.
    # The following function that estimates the initial frequency to
    # generate a waveform with given time to merger needs
    # the file at
    # https://git.ligo.org/lscsoft/lalsuite-extra/-/blob/master/data/lalsimulation/SEOBNRv4ROM_v2.0.hdf5
    # to be present at LAL_DATA_PATH
    # TODO: Replace this function with one from Phenom models
    q = zero_ecc_kwargs["q"]
    m1SI = q * M / (1 + q) * lal.MSUN_SI
    m2SI = M / (1 + q) * lal.MSUN_SI
    s1z = zero_ecc_kwargs["chi1"][2]
    s2z = zero_ecc_kwargs["chi2"][2]
    f0 = lalsim.SimIMRSEOBNRv4ROMFrequencyOfTime(
        inspiralTime, m1SI, m2SI, s1z, s2z)
    # convert to omega and make dimensionless
    Momega0_zeroecc = f0 * time_dimless_to_mks(M) * np.pi
    zero_ecc_kwargs["Momega0"] = Momega0_zeroecc

    dataDict_zeroecc = load_waveform(**zero_ecc_kwargs)
    t_zeroecc = dataDict_zeroecc["t"]

    # if f0 is too small and generate too long zero ecc waveform
    # report that
    if -t_zeroecc[0] >= - 2 * nr_dataDict["t"][0]:
        warnings.warn("zeroecc waveform is too long. It's "
                      f"{t_zeroecc[0]/nr_dataDict['t'][0]:.2f}"
                      " times the ecc waveform.")
    # We need the zeroecc modes to be long enough, at least the same length
    # as the eccentric one to get the residual amplitude correctly.
    # In case the zeroecc waveform is not long enough we reduce the
    # initial Momega0 by a factor of 2 and generate the waveform again
    # NEED A BETTER SOLUTION to this later
    num_tries = 0
    while t_zeroecc[0] > nr_dataDict["t"][0]:
        zero_ecc_kwargs["Momega0"] = zero_ecc_kwargs["Momega0"] / 2
        dataDict_zeroecc = load_waveform(**zero_ecc_kwargs)
        t_zeroecc = dataDict_zeroecc["t"]
        num_tries += 1
    if num_tries >= 2:
        warnings.warn("Too many tries to reset Momega0 for generating"
                      " zeroecc modes. Total number of tries = "
                      f"{num_tries}")
    hlm_zeroecc = dataDict_zeroecc["hlm"]
    # Finally we want to return zeroecc data only about the length of the
    # eccentric waveform and truncate the rest of the waveform to avoid
    # wasting computing resources
    start_zeroecc_idx = np.argmin(
        np.abs(t_zeroecc - nr_dataDict["t"][0])) - 10
    for key in hlm_zeroecc.keys():
        hlm_zeroecc[key] = hlm_zeroecc[key][start_zeroecc_idx:]

    return {"t_zeroecc": t_zeroecc[start_zeroecc_idx:],
            "hlm_zeroecc": hlm_zeroecc}


def reomve_junk_from_nr_data(t, modes_dict, num_orbits_to_remove_as_junk):
    """Remove junk from beginning of NR data.

    Parameters:
    ----------
    t:
        Time array for the NR data.
    modes_dict:
        Dictionary containing modes array.
    num_orbits_to_remove_as_junk:
        Number of orbits to remove as junk from the begining of NR data.

    Returns:
    t_clean:
        Time array corresponding to clean NR data.
    modes_dict_clean:
        modes_dict with `num_orbits_to_remove_as_junk` orbits removed from the
        begining of modes array.
    """
    phase22 = - np.unwrap(np.angle(modes_dict[(2, 2)]))
    # one orbit corresponds to 4pi change in 22 mode
    # phase
    idx_junk = np.argmin(
        np.abs(
            phase22 - (
                phase22[0]
                + num_orbits_to_remove_as_junk * 4 * np.pi)))
    t_clean = t[idx_junk:]
    modes_dict_clean = {}
    for key in modes_dict:
        modes_dict_clean[key] = modes_dict[key][idx_junk:]

    return t_clean, modes_dict_clean


def load_h22_from_EOBfile(EOB_file):
    """Load data from EOB files."""
    fp = h5py.File(EOB_file, "r")
    t_ecc = fp['data/t'][:]
    amp22_ecc = fp['data/hCoOrb/Amp_l2m2'][:]
    phi22_ecc = fp['data/hCoOrb/phi_l2m2'][:]

    t_nonecc = fp['data/t'][:]
    amp22_nonecc = fp['nonecc_data/hCoOrb/Amp_l2m2'][:]
    phi22_nonecc = fp['nonecc_data/hCoOrb/phi_l2m2'][:]

    fp.close()
    dataDict = {"t": t_ecc, "hlm": amp22_ecc * np.exp(1j * phi22_ecc),
                "t_zeroecc": t_nonecc,
                "hlm_zeroecc": amp22_nonecc * np.exp(1j * phi22_nonecc)}
    return dataDict


def load_EOB_EccTest_file(**kwargs):
    """Load EOB files for testing EccDefinition.

    These files were
    generated using SEOBNRv4EHM model. Allowed kwargs are

    filepath:
        Path to the EOB file. No default. Required in kwargs.
    include_zero_ecc:
        If True, loads the quasicircular waveform modes also.
        This requires providing the path to quasicircular waveform
        file, see "filepath_zeroecc" below.
    filepath_zero_ecc:
        Path to the waveform file containing quasicircular waveform
        modes. Required only if include_zero_ecc is True.
        No default.
    """
    f = h5py.File(kwargs["filepath"], "r")
    t = f["t"][:]
    hlm = {(2, 2): f["(2, 2)"][:]}
    # make t = 0 at the merger
    t = t - peak_time_via_quadratic_fit(
        t,
        amplitude_using_all_modes(hlm))[0]
    dataDict = {"t": t, "hlm": hlm}
    if ('include_zero_ecc' in kwargs) and kwargs['include_zero_ecc']:
        if "filepath_zero_ecc" not in kwargs:
            raise Exception("Mus provide file path to zero ecc waveform.")
        zero_ecc_kwargs = kwargs.copy()
        zero_ecc_kwargs["filepath"] = kwargs["filepath_zero_ecc"]
        zero_ecc_kwargs["include_zero_ecc"] = False
        dataDict_zero_ecc = load_EOB_EccTest_file(**zero_ecc_kwargs)
        t_zeroecc = dataDict_zero_ecc["t"]
        hlm_zeroecc = dataDict_zero_ecc["hlm"]
        dataDict.update({"t_zeroecc": t_zeroecc,
                         "hlm_zeroecc": hlm_zeroecc})
    return dataDict


def load_EOB_waveform(**kwargs):
    """Load EOB waveform."""
    # check kwargs
    allowed_kwargs = ["filepath", "filepath_zero_ecc", "include_zero_ecc"]
    for kw in kwargs:
        if kw not in allowed_kwargs:
            raise KeyError(f"{kw} is not a valid keyword."
                           f" Must be one of {allowed_kwargs}")
    if kwargs["filepath"] is None:
        raise Exception("Must provide file path to EOB waveform")
    if "EccTest" in kwargs["filepath"]:
        return load_EOB_EccTest_file(**kwargs)
    elif "Case" in kwargs["filepath"]:
        return load_h22_from_EOBfile(**kwargs)
    else:
        raise Exception("Unknown filepath pattern.")


def load_lvcnr_hack(**kwargs):
    """Load 22 mode from lvcnr files using h5py and Interpolation.

    NOTE: This is not the recommended way to load lvcnr files.
    Use load_lvcnr for that. Currently the load_lvcnr function
    has some issues where it fails to load due to too low f_low,
    or takes too long to load or loads only last few cycles.

    This is a simple hack to load the NR files using h5py and then
    interpolate the data. Also we only load 22 modes here for simiplicity.
    This function is mostly for testing measurement of eccentricity
    of NR waveforms.

    parameters:
    ----------
    kwargs: Could be the followings
    filepath: str
        Path to lvcnr file.

    deltaTOverM: float
        Time step. The loaded data will be interpolated using this time step.
        Default is 0.1

    include_zero_ecc: bool
        If True returns PhenomT waveform mode for same set of parameters
        except eccentricity set to zero. Default is True.

    num_orbits_to_remove_as_junk: float
        Number of orbits to throw away as junk from the begining of the NR
        data. Default is 2.

    returns:
    -------
        Dictionary of modes dict, parameter dict and also zero ecc mode dict if
        include_zero_ecc is True.

    t:
        Time array.
    hlm:
        Dictionary of modes.
    params_dict:
        Dictionary of parameters.
    Optionally,
    t_zeroecc:
        Time array for zero ecc modes.
    hlm_zeroecc:
        Mode dictionary for zero eccentricity.
    """
    default_kwargs = {"filepath": None,
                      "deltaTOverM": 0.1,
                      "include_zero_ecc": True,
                      "num_orbits_to_remove_as_junk": 2}

    kwargs = check_kwargs_and_set_defaults(kwargs, default_kwargs,
                                           "lvcnr kwargs")
    f = h5py.File(kwargs["filepath"])
    t_for_amp22 = f["amp_l2_m2"]["X"][:]
    amp22 = f["amp_l2_m2"]["Y"][:]

    t_for_phase22 = f["phase_l2_m2"]["X"][:]
    phase22 = f["phase_l2_m2"]["Y"][:]

    tstart = max(t_for_amp22[0], t_for_phase22[0])
    tend = min(t_for_amp22[-1], t_for_phase22[-1])

    t_interp = np.arange(tstart, tend, kwargs["deltaTOverM"])

    # NOTE: The data were downsampled using romspline
    # (https://arxiv.org/abs/1611.07529), which uses higher order splines as
    # appropriate, but we are now upsampling with only cubic splines.
    # This can lead to inaccuracies.
    amp22_interp = interpolate(t_interp, t_for_amp22, amp22)
    phase22_interp = interpolate(t_interp, t_for_phase22, phase22)
    h22_interp = amp22_interp * np.exp(1j * phase22_interp)

    # remove junk data from the beginning
    t, modes_dict = reomve_junk_from_nr_data(
        t_interp,
        {(2, 2): h22_interp},
        kwargs["num_orbits_to_remove_as_junk"])

    return_dict = {"t": t,
                   "hlm": modes_dict}

    # params
    s1x = f.attrs["spin1x"]
    s1y = f.attrs["spin1y"]
    s1z = f.attrs["spin1z"]
    s2x = f.attrs["spin2x"]
    s2y = f.attrs["spin2y"]
    s2z = f.attrs["spin2z"]
    m1 = f.attrs["mass1"]
    m2 = f.attrs["mass2"]
    ecc = f.attrs["eccentricity"]
    mean_ano = f.attrs["mean_anomaly"]
    f.close()
    params_dict = {"q": m1/m2,
                   "chi1": [s1x, s1y, s1z],
                   "chi2": [s2x, s2y, s2z],
                   "ecc": ecc,
                   "mean_ano": mean_ano,
                   "deltaTOverM": t_interp[1] - t_interp[0],
                   }

    return_dict.update({"params_dict": params_dict})

    if ("include_zero_ecc" in kwargs) and kwargs["include_zero_ecc"]:
        dataDict_zeroecc = get_zeroecc_dataDict_for_lvcnr(return_dict)
        return_dict.update(dataDict_zeroecc)

    return return_dict


def load_EMRI_waveform(**kwargs):
    """Load EMRI waveforms data.

    kwargs dictionary could contain
    filepath: str
        Path to the eccentric EMRI waveform. Default is None which would raise
        an error.
    include_zero_ecc: bool
        If true, load circular EMRI waveform that has the same set of
        parameters as used for eccentric EMRI except the eccentricity being set
        to zero. Default is False.
    filepath_zero_ecc: str
        Path to the circular EMRI waveform. If None, a filepath would be
        generated based on the filepath of the eccentric waveform.
        Default is None.
    start_time: float
        Since the EMRI waveforms could be very long, one can opt to load the
        waveform only from start_time, where start_time is to provided
        following the convention of merger being at t=0. Since EMRI waveform
        does not include an actual merger, t=0 corresponds to the global
        maximum of the amplitude.
        Default is None.
        If None, start_time would be the time at the start of the waveform.
    end_time: float
        Similar to start_time, one could provide an end_time, time up to which
        the waveform is to be loaded. Default is None, which would set the
        end_time to the time of the global maximum.
    deltaT: float
        If provided, it would be used to interpolate the waveform with this
        time step. Default is None which would not do any interpolation.
    include_geodesic_ecc: bool
        If True, loads geodesic eccentricity data. Default is False.
    """
    default_kwargs = {"filepath": None,
                      "include_zero_ecc": False,
                      "filepath_zero_ecc": None,
                      "start_time": None,
                      "end_time": None,
                      "deltaT": None,
                      "include_geodesic_ecc": False}
    kwargs = check_kwargs_and_set_defaults(
        kwargs,
        default_kwargs,
        "EMRI kwargs",
        "gw_eccentricity.load_data.load_EMRI_waveform")
    if kwargs["filepath"] is None:
        raise KeyError("path to the eccentric EMRI waveform cannot be None.")
    emri_data = h5py.File(kwargs["filepath"], "r")["Dataset1"]
    t = emri_data[:, 0]
    h22 = emri_data[:, 1] + 1j * emri_data[:, 2]
    tpeak = peak_time_via_quadratic_fit(
        t,
        amplitude_using_all_modes({(2, 2): h22}))[0]
    t -= tpeak
    if kwargs["start_time"] is not None:
        start = np.argmin(np.abs(t - kwargs["start_time"]))
    else:
        start = 0
    if kwargs["end_time"] is not None:
        end = np.argmin(np.abs(t - kwargs["end_time"]))
    else:
        end = -1
    t_new = t[start: end]
    h22_new = h22[start: end]
    dataDict = {"t": t_new,
                "hlm": {(2, 2): h22_new}}
    if kwargs["deltaT"] is not None:
        t_interp = np.arange(t_new[0], t_new[-1], kwargs["deltaT"])
        # make t_interp within the bounds of t_new to avoild extrapolation
        t_interp = t_interp[np.logical_and(t_interp >= t_new[0],
                                           t_interp <= t_new[-1])]
        amp22_interp = interpolate(t_interp, t_new, np.abs(h22_new))
        phase22_interp = interpolate(
            t_interp, t_new, np.unwrap(np.angle(h22_new)))
        h22_interp = amp22_interp * np.exp(1j * phase22_interp)
        dataDict["t"] = t_interp
        dataDict["hlm"] = {(2, 2): h22_interp}

    if kwargs["include_zero_ecc"]:
        if kwargs["filepath_zero_ecc"] is None:
            idx = kwargs["filepath"].find("e0")
            kwargs["filepath_zero_ecc"] = (
                kwargs["filepath"][:idx] + "e0.000.h5")
        kwargs_zero_ecc = {
            "filepath": kwargs["filepath_zero_ecc"],
            "include_zero_ecc": False}
        dataDict_zero_ecc = load_EMRI_waveform(**kwargs_zero_ecc)
        dataDict.update({
            "t_zeroecc": dataDict_zero_ecc["t"],
            "hlm_zeroecc": {(2, 2): dataDict_zero_ecc["hlm"][(2, 2)]
                            / np.sqrt(2*np.pi)}})
    if kwargs["include_geodesic_ecc"]:
        e_geodesic_file = kwargs["filepath"][:-3] + "_ecc.h5"
        e_geodesic_data = h5py.File(e_geodesic_file, "r")["Dataset1"]
        e_geodesic = e_geodesic_data[:, 1][start: end]
        dataDict.update({"e_geodesic": e_geodesic})
        if kwargs["deltaT"] is not None:
            e_geodesic_interp = interpolate(
                t_interp, t_new, np.abs(e_geodesic))
            dataDict.update({"e_geodesic": e_geodesic_interp})
    return dataDict
