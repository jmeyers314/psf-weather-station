"""Util functions for psfws."""

import numpy as np
import pandas as pd
from scipy.interpolate import interp1d
from sklearn import gaussian_process as gp
from scipy.integrate import trapz


def initialize_location(location):
    """Given location identifier, return ground altitude and dome height.

    Parameters
    ----------
    location : str or dict
        Valid options for : 'cerro-paranal', 'cerro-pachon', 'cerro-telolo', 
        'mauna-kea', and 'la-palma'.
        To customize to another observatory, input instead a dict with keys
        'altitude' (value in km) and 'height' (optional: dome height in m. 
        If not given, will default to 10m)

    """
    # custom usage
    if type(location) == dict:
        h0 = location['altitude']
        try:
            h_tel = location['height']
        except KeyError:
            h_tel = 10

    else:
        ground_altitude = {'cerro-pachon': 2.715, 'mauna-kea': 4.2, 
                           'cerro-telolo': 2.2, 'la-palma': 2.4,
                           'cerro-paranal': 2.64}
        h0 = ground_altitude[location]
        h_tel = 10 # in m
        
    return h0, h_tel/1000

def ground_cn2_model(params, h):
    """Return power law model for Cn2 as function of elevation."""
    logcn2 = params['A'] + params['p'] * np.log10(h)
    return 10**(logcn2)

def find_max_median(x, h_old, h_new):
    """Find max of median of array x by interpolating datapoints."""
    # interpolate median x to smoothly spaced new h values
    x_median = np.median(x, axis=0)
    x_interp = interpolate(h_old, x_median, h_new, kind='cubic')
    
    # find maximum of interpolated x *above 2km*, to avoid ground effects
    h_max = h_new[h_new>2][np.argmax(x_interp[h_new>2])]

    return h_max

def process_telemetry(telemetry):
    """Process telemetry measurements of speed/direction.

    Parameters
    ----------
    telemetry : dict 
        Dict with keys 'wind_direction' and 'wind_speed', values are pandas 
        series obejcts of wind speed/direction measurements
    
    Returns
    ------- 
    telemetry: dict
        Dict holding a dataframe each for wind directions and speeds, 
        keys 'dir' and 'speed'
    tel_masks : dict
        Dict with masks for unwanted values (e.g. zeros) in above data

    """
    tel_dir = telemetry['wind_direction']
    tel_speed = telemetry['wind_speed']

    # find masks for telemetry values that are zero or, for speeds, >40
    speed_mask = tel_speed.index[tel_speed.apply(lambda x: x!=0 and x<40)]
    dir_mask = tel_dir.index[tel_dir.apply(lambda x: x!=0)]
    cp_masks = {'speed': speed_mask, 'dir': tel_dir.index}

    return {'dir': tel_dir, 'speed': tel_speed}, cp_masks

def to_direction(x, y):
    """Calculate wind direction from u,v components.

    some more description of what this does.
    """
    d = np.arctan2(y, x) * (180/np.pi)
    return (d + 180) % 360

def to_components(s, d):
    """Calculate the wind velocity components given speed/direction.

    some more description of what this does.
    """
    v = s * np.cos((d - 180) * np.pi/180)
    u = s * np.sin((d - 180) * np.pi/180)
    return {'v': v, 'u': u}

def process_gfs(gfs_df):
    """Process global forecasting system data to desired formats.

    input: dataframe of GFS obsevrations with 'u' and 'v' columns
    returns dataframe with: 
    - u and v altitudes reversed
    - new "speed" and "dir" columns added
    """
    not_daytime = gfs_df.index.hour!=12
    gfs_df = gfs_df.iloc[not_daytime].copy()

    gfs_df['u'] = [gfs_df['u'].values[i][::-1][:-5] for i in range(len(gfs_df))]
    gfs_df['v'] = [gfs_df['v'].values[i][::-1][:-5] for i in range(len(gfs_df))]

    gfs_df['speed'] = [np.hypot(gfs_df['u'].values[i], gfs_df['v'].values[i])
                       for i in range(len(gfs_df))]

    gfs_df['dir'] = [to_direction(gfs_df['v'].values[i], gfs_df['u'].values[i])
                     for i in range(len(gfs_df))]
    return gfs_df

def match_telemetry(speed, direction, gfs_dates):
    """Temporally match telemetry and GFS outputs.

    return matched speed/direction data associated with the gfs dates 
    specifically, return the median telemetry values in a +/- 30min interval 
    around each GFS point
    """
    n_gfs = len(gfs_dates)

    speed_ids = [speed.index[abs(gfs_dates[i] - speed.index) \
                             < pd.Timedelta('30min')] for i in range(n_gfs)]

    dir_ids = [direction.index[abs(gfs_dates[i] - direction.index) \
                               < pd.Timedelta('30min')] for i in range(n_gfs)]
    
    ids_to_keep = [i for i in range(n_gfs) \
                    if len(speed_ids[i])!=0 and len(dir_ids[i])!=0]
    
    matched_s = [np.median(speed.loc[speed_ids[i]]) \
                           for i in range(n_gfs) if i in ids_to_keep]
    matched_d = [np.median(direction.loc[dir_ids[i]]) \
                           for i in range(n_gfs) if i in ids_to_keep]

    return np.array(matched_s), np.array(matched_d), gfs_dates[ids_to_keep]

def interpolate(x, y, new_x, kind):
    """Interpolate 1D array to new x values.

    function to perform either cubic or GP interpolation of inputs
    returns interpolation new_y at positions new_x
    """
    if kind == 'cubic':
        f_y = interp1d(x, y, kind='cubic')
        new_y = f_y(new_x)
    elif kind == 'gp':
        gp = gaussian_process.GaussianProcessRegressor(normalize_y=True, 
                                         alpha=1e-3, n_restarts_optimizer=5)
        gp.fit(x.reshape(-1, 1), y.reshape(-1, 1))
        new_y = gp.predict(new_x.reshape(-1,1))
    return new_y

def hufnagel(z, v):
    """Calculte Hufnagel Cn2, input z in km, v in m/s.

    some more description of what this does.
    """
    if np.min(z) < 3:
        raise ValueError('hufnagel model only valid for height>3km')
    z = np.copy(z) * 1000 # to km
    tmp1 = 2.2e-53 * z**10 * (v / 27)**2 * np.exp(-z * 1e-3)
    tmp2 = 1e-16 * np.exp(-z * 1.5e-3)
    return tmp1 + tmp2

def integrate_in_bins(cn2, h, edges):
    """Integrate cn2 into altitude bins.

    :cn2: cn2 values from model outputs
    :h: h values of the input cn2
    """
    # make an equally spaced sampling in h across whole range:
    h_samples = np.linspace(edges[0] * 1000, edges[-1] * 1000, 1000)

    J=[]
    for i in range(len(edges)-1):
        # find the h samples that are within the altitude range of integration
        h_i = h_samples[(h_samples<edges[i+1] * 1000) & (h_samples>edges[i] * 1000)]
        # get Cn2 interpolation at those values of h
        cn2_i = np.exp(interpolate(h * 1000, np.log(cn2), h_i, kind='cubic'))
        # numerically integrate to find the J value for this bin
        J.append(trapz(cn2_i, h_i))

    return J

def smooth_direction(directions):
    """Convert U, V components of wind velocity to a direction.

    Returns the smoothest answer (i.e. shifts points +/- 360 for a smooth curve)
    """
    smooth_dir = np.zeros(directions.shape)
    smooth_dir[0] = directions[0]
    
    for i in range(smooth_dir.shape[0]-1):
        # make an array of options: next pt, next pt+360, next pt-360
        options = directions[i+1] + np.array([720, 360, 0, -360, -720])
        # find the option for next that has smallest jump from the current
        smoothest = np.argmin(abs(options - smooth_dir[i]))
        smooth_dir[i+1] = options[smoothest]

    # check the mean of the directions, bring up/down by 360 if needed
    while smooth_dir.mean() > 180:
        smooth_dir -= 360
    while smooth_dir.mean() < -180: 
        smooth_dir += 360
        
    return smooth_dir