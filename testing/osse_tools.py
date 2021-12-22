# Native packages
from math import radians, degrees, sin, cos, asin, acos, sqrt
import datetime
import sys
import os
import requests

# Third-party packages for data manipulation
import numpy as np
import pandas as pd
import xarray as xr

# Third-party packages for data interpolation
from scipy import interpolate
from xgcm import Grid

# Third-party packages for data visualizations
import matplotlib.pyplot as plt
from mpl_toolkits import mplot3d
from mpl_toolkits.mplot3d import axes3d


# import s3fs

from netrc import netrc
from urllib import request
from platform import system
from getpass import getpass
from http.cookiejar import CookieJar
from os.path import expanduser, join
import datetime
import gsw as sw
import numpy as np
import xgcm.grid
import netCDF4 as nc4

# ***This library includes*** 
# - setup_earthdata_login_auth
# - download_llc4320_data
# - compute_derived_fields
# - get_survey_track
# - survey_interp
# - great_circle

def setup_earthdata_login_auth(endpoint: str='urs.earthdata.nasa.gov'):
    netrc_name = "_netrc" if system()=="Windows" else ".netrc"
    try:
        username, _, password = netrc(file=join(expanduser('~'), netrc_name)).authenticators(endpoint)
    except (FileNotFoundError, TypeError):
        print('Please provide your Earthdata Login credentials for access.')
        print('Your info will only be passed to %s and will not be exposed in Jupyter.' % (endpoint))
        username = input('Username: ')
        password = getpass('Password: ')
    manager = request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, endpoint, username, password)
    auth = request.HTTPBasicAuthHandler(manager)
    jar = CookieJar()
    processor = request.HTTPCookieProcessor(jar)
    opener = request.build_opener(auth, processor)
    request.install_opener(opener)
    
    
def download_llc4320_data(RegionName, datadir, start_date, ndays):
    """
    Check for existing llc4320 files in 'datadir' and download if they aren't found
    inputs XXX
    """
    ShortName = "MITgcm_LLC4320_Pre-SWOT_JPL_L4_" + RegionName + "_v1.0"
    date_list = [start_date + datetime.timedelta(days=x) for x in range(ndays)]
    target_files = [f'LLC4320_pre-SWOT_{RegionName}_{date_list[n].strftime("%Y%m%d")}.nc' for n in range(ndays)] # list of files to check for/download
    setup_earthdata_login_auth()
    
    # https access for each target_file
    url = "https://archive.podaac.earthdata.nasa.gov/podaac-ops-cumulus-protected"
    https_accesses = [f"{url}/{ShortName}/{target_file}" for target_file in target_files]
#     print(https_accesses)
    

#     def begin_s3_direct_access():
#     """Returns s3fs object for accessing datasets stored in S3."""
#     response = requests.get("https://archive.podaac.earthdata.nasa.gov/s3credentials").json()
#     return s3fs.S3FileSystem(key=response['accessKeyId'],
#                              secret=response['secretAccessKey'],
#                              token=response['sessionToken'], 
#                              client_kwargs={'region_name':'us-west-2'})

    # list of dataset objects
    dds = []
    for https_access,target_file in zip(https_accesses,target_files):
        print(target_file) # print file name

        if not(os.path.isfile(datadir + target_file)):
            filename_dir = os.path.join(datadir, target_file)
            request.urlretrieve(https_access, filename_dir)
           
            
def compute_derived_fields(RegionName, datadir, start_date, ndays):
    """
    Check for derived files in 'datadir'/derived and compute if the files don't exist
    """
    # directory to save derived data to - create if doesn't exist
    derivedir = datadir + 'derived/'
    if not(os.path.isdir(derivedir)):
        os.mkdir(derivedir)
        
    # files to load:
    date_list = [start_date + datetime.timedelta(days=x) for x in range(ndays)]
    target_files = [f'{datadir}LLC4320_pre-SWOT_{RegionName}_{date_list[n].strftime("%Y%m%d")}.nc' for n in range(ndays)] # list target files
    
    # list of derived files:
    derived_files = [f'{derivedir}LLC4320_pre-SWOT_{RegionName}_derived-fields_{date_list[n].strftime("%Y%m%d")}.nc' for n in range(ndays)] # list target files

        
    # loop through input files, then compute steric height, vorticity, etc. on the i/j grid
    fis = range(len(target_files))
    
    cnt = 0 # count
    for fi in fis:
        # input filename:
        thisf=target_files[fi]
        # output filename:
        fnout = thisf.replace(RegionName + '_' , RegionName + '_derived-fields_')
        fnout = fnout.replace(RegionName + '/' , RegionName + '/derived/')
        # check if output file already exists
        if (not(os.path.isfile(fnout))):   
            print('computing derived fields for', thisf) 
            # load file:
            ds = xr.open_dataset(thisf)
            
            # -------
            # first time through the loop, load reference profile:
            # load a single file to get coordinates
            if cnt==0:
                # mean lat/lon of domain
                xav = ds.XC.isel(j=0).mean(dim='i')
                yav = ds.YC.isel(i=0).mean(dim='j')

                # for vorticity calculation, build the xgcm grid:
                # see https://xgcm.readthedocs.io/en/latest/xgcm-examples/02_mitgcm.html
                grid = xgcm.Grid(ds, coords={'X':{'center': 'i', 'left': 'i_g'}, 
                             'Y':{'center': 'j', 'left': 'j_g'},
                             'T':{'center': 'time'},
                             'Z':{'center': 'k'}})

                # load reference file of argo data
                # NOTE: could update to pull from ERDDAP or similar
                argoclimfile = '/data1/argo/argo_CLIM_3x3.nc'
                argods = xr.open_dataset(argoclimfile,decode_times=False) 
                # reference profiles: annual average Argo T/S using nearest neighbor
                Tref = argods["TEMP"].sel(LATITUDE=yav,LONGITUDE=xav, method='nearest').mean(dim='TIME')
                Sref = argods["SALT"].sel(LATITUDE=yav,LONGITUDE=xav, method='nearest').mean(dim='TIME')
                # SA and CT from gsw:
                # see example from https://discourse.pangeo.io/t/wrapped-for-dask-teos-10-gibbs-seawater-gsw-oceanographic-toolbox/466
                Pref = xr.apply_ufunc(sw.p_from_z, -argods.LEVEL, yav)
                Pref.compute()
                SAref = xr.apply_ufunc(sw.SA_from_SP, Sref, Pref, xav, yav,
                                       dask='parallelized', output_dtypes=[Sref.dtype])
                SAref.compute()
                CTref = xr.apply_ufunc(sw.CT_from_pt, Sref, Tref, # Theta is potential temperature
                                       dask='parallelized', output_dtypes=[Sref.dtype])
                CTref.compute()
                Dref = xr.apply_ufunc(sw.density.rho, SAref, CTref, Pref,
                                    dask='parallelized', output_dtypes=[Sref.dtype])
                Dref.compute()
                cnt = cnt+1
                print()
            # -------
            # 
            # --- compute steric height in steps ---
            # 0. create datasets for variables of interest:
            ss = ds.Salt
            tt = ds.Theta
            pp = xr.DataArray(sw.p_from_z(ds.Z,ds.YC))
            
            # 1. compute absolute salinity and conservative temperature
            sa = xr.apply_ufunc(sw.SA_from_SP, ss, pp, xav, yav, dask='parallelized', output_dtypes=[ss.dtype])
            sa.compute()
            ct = xr.apply_ufunc(sw.CT_from_pt, sa, tt, dask='parallelized', output_dtypes=[ss.dtype])
            ct.compute()
            dd = xr.apply_ufunc(sw.density.rho, sa, ct, pp, dask='parallelized', output_dtypes=[ss.dtype])
            dd.compute()
            # 2. compute specific volume anomaly: gsw.density.specvol_anom_standard(SA, CT, p)
            sva = xr.apply_ufunc(sw.density.specvol_anom_standard, sa, ct, pp, dask='parallelized', output_dtypes=[ss.dtype])
            sva.compute()
            # 3. compute steric height = integral(0:z1) of Dref(z)*sva(z)*dz(z)
            # - first, interpolate Dref to the model pressure levels
            Drefi = Dref.interp(LEVEL=-ds.Z)
            dz = -ds.Z_bnds.diff(dim='nb').drop_vars('nb').squeeze() # distance between interfaces

            # steric height computation (summation/integral)
            # - increase the size of Drefi and dz to match the size of sva
            Db = Drefi.broadcast_like(sva)
            dzb = dz.broadcast_like(sva)
            dum = Db * sva * dzb
            sh = dum.cumsum(dim='k') 
            # this gives sh as a 3-d variable, (where the depth dimension 
            # represents the deepest level from which the specific volume anomaly was interpolated)
            # - but in reality we just want the SH that was determined by integrating over
            # the full survey depth, which gives a 2-d output:
            sh_true = dum.sum(dim='k') 
            

            # --- compute vorticity using xgcm and interpolate to X, Y
            # see https://xgcm.readthedocs.io/en/latest/xgcm-examples/02_mitgcm.html
            vorticity = (grid.diff(ds.V*ds.DXG, 'X') - grid.diff(ds.U*ds.DYG, 'Y'))/ds.RAZ
            vorticity = grid.interp(grid.interp(vorticity, 'X', boundary='extend'), 'Y', boundary='extend')

            # --- save derived fields in a new file
            # - convert sh and zeta to datasets
            dout = vorticity.to_dataset(name='vorticity')
            sh_ds = sh.to_dataset(name='steric_height')
            dout = dout.merge(sh_ds)
            sh_true_ds = sh_true.to_dataset(name='steric_height_true')
            dout = dout.merge(sh_true_ds)
            # add/rename the Argo reference profile variables
            tref = Tref.to_dataset(name='Tref')
            tref = tref.merge(Sref).rename({'SALT': 'Sref'}).\
                rename({'LEVEL':'zref','LATITUDE':'yav','LONGITUDE':'xav'}).\
                drop_vars({'i','j'})
            # - add ref profiles to dout and drop uneeded vars/coords
            dout = dout.merge(tref).drop_vars({'LONGITUDE','LATITUDE','LEVEL','i','j'})
#             dout = dout.merge(tref).drop_vars({'LONGITUDE','LATITUDE','i','j'})
            
            # - save netcdf file with derived fields
            netcdf_fill_value = nc4.default_fillvals['f4']
            dv_encoding = {}
            for dv in dout.data_vars:
                dv_encoding[dv]={'zlib':True,  # turns compression on\
                            'complevel':9,     # 1 = fastest, lowest compression; 9=slowest, highest compression \
                            'shuffle':True,    # shuffle filter can significantly improve compression ratios, and is on by default \
                            'dtype':'float32',\
                            '_FillValue':netcdf_fill_value}
            # save to a new file
            print(' ... saving to ', fnout)
            dout.to_netcdf(fnout,format='netcdf4',encoding=dv_encoding)

        
    
def get_survey_track(ds, sampling_details):
     
    """
    Returns the track (lat, lon, depth, time) and indices (i, j, k, time) of the 
    sampling trajectory based on the type of sampling (sampling_details[SAMPLING_STRATEGY]), 
    and sampling details (in dict sampling_details), which includes
    number of days, waypoints, and depth range, horizontal and vertical platform speed
    -- these can be typical values (default) or user-specified (optional)
    """
    
    # Change time from datetime to integer
    ds = ds.assign_coords(time=np.linspace(0,ds.time.size-1, num=ds.time.size)) # time is now in hours
    survey_time_total = (ds.time.values.max() - ds.time.values.min()) * 3600 # (seconds) - limits the survey to a total time

    # Convert lon, lat and z to index i, j and k with f_x, f_y and f_z
    # XC, YC and Z are the same at all times, so select a single time
    X = ds.XC.isel(time=0) 
    Y = ds.YC.isel(time=0)
    i = ds.i
    j = ds.j
    z = ds.Z.isel(time=0)
    k = ds.k
    f_x = interpolate.interp1d(X[0,:].values, i)
    f_y = interpolate.interp1d(Y[:,0].values, j)
    f_z = interpolate.interp1d(z, k, bounds_error=False)

    # Get boundaries and center of model region
    model_boundary_n = Y.max().values
    model_boundary_s = Y.min().values
    model_boundary_w = X.min().values
    model_boundary_e = X.max().values
    model_xav = ds.XC.isel(time=0, j=0).mean(dim='i').values
    model_yav = ds.YC.isel(time=0, i=0).mean(dim='j').values
    # --------- define sampling -------
    SAMPLING_STRATEGY = sampling_details['SAMPLING_STRATEGY']
    # ------ default sampling parameters -----
    defaults = {'AT_END' : 'terminate'}  # behaviour at and of trajectory: 'repeat' or 'terminate'. (could also 'restart'?)
    # default values depend on the sampling type
    # typical speeds and depth ranges based on platform 
    if SAMPLING_STRATEGY == 'sim_uctd':
        #PATTERN = sampling_details['PATTERN']
        # typical values for uctd sampling:
        defaults['zrange'] = [-5, -500] # depth range of profiles (down is negative)
        defaults['hspeed'] = 5 # platform horizontal speed in m/s
        defaults['vspeed'] = 1 # platform vertical (profile) speed in m/s (NOTE: may want different up/down speeds)  
    elif SAMPLING_STRATEGY == 'sim_glider':
        #PATTERN = sampling_details['PATTERN']
        defaults['zrange'] = [-1, -1000] # depth range of profiles (down is negative)
        defaults['hspeed'] = 0.25 # platform horizontal speed in m/s
        defaults['vspeed'] = 0.1 # platform vertical (profile) speed in m/s      
    elif SAMPLING_STRATEGY == 'sim_mooring':
        defaults['xmooring'] = model_xav # default lat/lon is the center of the domain
        defaults['ymooring'] = model_yav
        defaults['zmooring_TS'] = [-1, -10, -50, -100] # depth of T/S instruments
        defaults['zmooring_TS'] = [-1, -10, -50, -100] # depth of T/S instruments
        defaults['zmooring_UV'] = [-1, -10, -50, -100] # depth of U/V instruments
    elif SAMPLING_STRATEGY == 'trajectory_file':
        # load file
        traj = xr.open_dataset(sampling_details['trajectory_file'])
        xwaypoints = traj.xwaypoints.values
        ywaypoints = traj.ywaypoints.values
        zrange = traj.zrange.values # depth range of profiles (down is negative)
        hspeed = traj.hspeed.values # platform horizontal speed in m/s
        vspeed = traj.vspeed.values # platform vertical (profile) speed in m/s
        PATTERN = traj.attrs['pattern']
    else:
        # if SAMPLING_STRATEGY not specified, return an error
        print('error: SAMPLING_STRATEGY ' + SAMPLING_STRATEGY + ' invalid')
        return -1
    
    
    # merge defaults & sampling_details
    # - by putting sampling_details second, items that appear in both dicts are taken from sampling_details: 
    sampling_details = {**defaults, **sampling_details}
    
#      # DELETE BELOW   
#     AT_END = 'terminate' # behaviour at and of trajectory: 'repeat' or 'terminate'. (could also 'restart'?)
#     # default values depend on the sampling type
#     # typical speeds and depth ranges based on platform 
#     if SAMPLING_STRATEGY == 'sim_uctd':
#         PATTERN = sampling_details['PATTERN']
#         # typical values for uctd sampling:
#         zrange = [-5, -500] # depth range of profiles (down is negative)
#         hspeed = 5 # platform horizontal speed in m/s
#         vspeed = 1 # platform vertical (profile) speed in m/s (NOTE: may want different up/down speeds)  
#     elif SAMPLING_STRATEGY == 'sim_glider':
#         PATTERN = sampling_details['PATTERN']
#         zrange = [-1, -1000] # depth range of profiles (down is negative)
#         hspeed = 0.25 # platform horizontal speed in m/s
#         vspeed = 0.1 # platform vertical (profile) speed in m/s      
#     elif SAMPLING_STRATEGY == 'sim_mooring':
#         xmooring = model_xav # default lat/lon is the center of the domain
#         ymooring = model_yav
#         zmooring_TS = [-1, -10, -50, -100] # depth of T/S instruments
#         zmooring_TS = [-1, -10, -50, -100] # depth of T/S instruments
#         zmooring_UV = [-1, -10, -50, -100] # depth of U/V instruments
#     elif SAMPLING_STRATEGY == 'trajectory_file':
#         # load file
#         traj = xr.open_dataset(sampling_details['trajectory_file'])
#         xwaypoints = traj.xwaypoints.values
#         ywaypoints = traj.ywaypoints.values
#         zrange = traj.zrange.values # depth range of profiles (down is negative)
#         hspeed = traj.hspeed.values # platform horizontal speed in m/s
#         vspeed = traj.vspeed.values # platform vertical (profile) speed in m/s
#         PATTERN = traj.attrs['pattern']
#     else:
#         # if SAMPLING_STRATEGY not specified, return an error
#         print('error: SAMPLING_STRATEGY ' + SAMPLING_STRATEGY + ' invalid')
#         return -1
   
#     # ---- sampling specified in "sampling_details" always overrides the above defaults: 
#     list_of_sampling_details = ['zrange','hspeed','vspeed','AT_END','xmooring','ymooring',
#                             'zmooring_TS','zmooring_UV','dzmooring_TS','dzmooring_UV'];
#     sampling_details
#     for sd in list_of_sampling_details:
#         if sd in sampling_details:
#             print(1)
#         else:
#             print(0)
#             sampling_details[sd] = zmooring_TS
        
    # *** NOT WORKING - can't just pass exec variables :( ***
    # obvi this isn't the right way to rename variables ... 
    # probably should just call the dict later
#     for sd in list_of_sampling_details:
#         if sd in sampling_details and sampling_details[sd] is not None:
#             exec(sd + ' = sampling_details["' + sd + '"]',None, globals())
#             exec(sd + ' = sampling_details["' + sd + '"]')
#             print('a = sampling_details["' + sd + '"]')
#             exec('a = 3',None, globals() )
#     print(sampling_details["zmooring_TS"])
#     zmooring_TS = sampling_details["zmooring_TS"]
#     print(zmooring_TS)
#     print(a)
    
    # for moorings, location is fixed so a set of waypoints is not needed.
    if SAMPLING_STRATEGY == 'sim_mooring':
        # time sampling is one per model timestep
#         ts = ds.time.values / 24 # convert from hours to days
        ts = ds.time.values # in hours
        n_samples = ts.size
        n_profiles = n_samples
        # same sampling for T/S/U/V for now. NOTE: change this later!        
        zs = np.tile(sampling_details['zmooring_TS'], int(n_samples)) # sample depths * # of samples 
        xs = sampling_details['xmooring'] * np.ones(np.size(zs))  # all samples @ same x location
        ys = sampling_details['ymooring'] * np.ones(np.size(zs))  # all samples @ same y location
        ts = np.repeat(ts, len(sampling_details['zmooring_TS']))  # tile to match size of other fields. use REPEAT, not TILE to get the interpolation right.

#         # depth sampling - different for TS and UV
#         zs_TS = np.tile(zmooring_TS, int(n_samples))
#         zs_UV = np.tile(zmooring_UV, int(n_samples))
#         xs_TS = xmooring * np.ones(np.size(zs_TS))
#         xs_UV = xmooring * np.ones(np.size(zs_UV))
#         ys_TS = ymooring * np.ones(np.size(zs_TS))
#         ys_UV = ymooring * np.ones(np.size(zs_UV))
#         ts_TS = np.tile(ts, int(n_samples))
        
        
#         lon_TS = xr.DataArray(xs_TS,dims='points'),
#         lat_TS = xr.DataArray(ys_TS,dims='points'),
#         dep_TS = xr.DataArray(zs_TS,dims='points'),
#         time_TS = xr.DataArray(ts,dims='points')
               
#         lon = lon_TS
#         lat = lat_TS
#         dep = dep_TS
#         time = time_TS
    
    else:
        # --- if not a mooring, define waypoints  
    
        # define x & y waypoints and z range
        # xwaypoints & ywaypoints must have the same size
        if sampling_details['PATTERN'] == 'lawnmower':
            # "mow the lawn" pattern - define all waypoints
            if not(SAMPLING_STRATEGY == 'trajectory_file'):
                # generalize the survey for this region
                xwaypoints = model_boundary_w + 1 + [0, 0, 0.5, 0.5, 1, 1, 1.5, 1.5, 2, 2]
                ywaypoints = model_boundary_s + [1, 2, 2, 1, 1, 2, 2, 1, 1, 2, 2]
        elif sampling_details['PATTERN'] == 'back-forth':
            if not(SAMPLING_STRATEGY == 'trajectory_file'):
                # repeated back & forth transects - define the end-points
                xwaypoints = model_xav + [-1, 1]
                ywaypoints = model_yav + [-1, 1]
            # repeat waypoints based on total # of transects: 
            dkm_per_transect = great_circle(xwaypoints[0], ywaypoints[0], xwaypoints[1], ywaypoints[1]) # distance of one transect in km
            t_per_transect = dkm_per_transect * 1000 / hspeed # time per transect, seconds
            num_transects = np.round(survey_time_total / t_per_transect)
            for n in np.arange(num_transects):
                xwaypoints = np.append(xwaypoints, xwaypoints[-2])
                ywaypoints = np.append(ywaypoints, ywaypoints[-2])

        # vertical resolution
        # for now, use a constant  vertical resolution (NOTE: could make this a variable)
        zresolution = 1 # meters
        # max depth can't be deeper than the max model depth in this region
        sampling_details['zrange'][1] = -np.min([-sampling_details['zrange'][1], ds.Depth.isel(time=1).max(...).values])        
        zprofile = np.arange(sampling_details['zrange'][0],sampling_details['zrange'][1],-zresolution) # depths for one profile
        ztwoway = np.append(zprofile,zprofile[-1::-1])
        # time resolution of sampling (dt):
        dt = zresolution / sampling_details['vspeed'] # sampling resolution in seconds
        # for each timestep dt 
        deltah = sampling_details['hspeed']*dt # horizontal distance traveled per sample
        deltav = sampling_details['vspeed']*dt # vertical distance traveled per sample

        # determine the sampling locations in 2-d space
        # - initialize sample locations xs, ys, zs, ts
        xs = []
        ys = []
        zs = []
        ts = []
        dkm_total = 0 
    

        for w in np.arange(len(xwaypoints)-1):
            # interpolate between this and the following waypoint:
            dkm = great_circle(xwaypoints[w], ywaypoints[w], xwaypoints[w+1], ywaypoints[w+1])
            # number of time steps (vertical measurements) between this and the next waypoint
            nstep = int(dkm*1000 / deltah) 
            yi = np.linspace(ywaypoints[w], ywaypoints[w+1], nstep)
            xi = np.linspace(xwaypoints[w], xwaypoints[w+1], nstep)
            xi = xi[0:-1] # remove last point, which is the next waypoint
            xs = np.append(xs, xi) # append
            yi = yi[0:-1] # remove last point, which is the next waypoint
            ys = np.append(ys, yi) # append
            dkm_total = dkm_total + dkm
            t_total = dkm_total * 1000 / sampling_details['hspeed'] # cumulative survey time to this point
            # cut off the survey after survey_time_total, if specified
            if t_total > survey_time_total:
                break

        # if at the end of the waypoints but time is less than the total, trigger AT_END behavior:
        if t_total < survey_time_total:
            if sampling_details['AT_END'] == 'repeat': 
                # start at the beginning again
                # determine how many times the survey repeats:
                num_transects = np.round(survey_time_total / t_total)
                xtemp = xs
                ytemp = ys
                # ***** HAVE TO ADD THE TRANSECT BACK TO THE START !!!
                for n in np.arange(num_transects):
                    xs = np.append(xs, xtemp)
                    ys = np.append(ys, ytemp)
            elif sampling_details['AT_END'] == 'reverse': 
                # turn around & go in the opposite direction
                # determine how many times the survey repeats:
                num_transects = np.round(survey_time_total / t_total)
                xtemp = xs
                ytemp = ys
                # append both a backward & another forward transect
                for n in np.arange(np.ceil(num_transects/2)):
                    xs = np.append(np.append(xs, xtemp[-2:1:-1]), xtemp)
                    ys = np.append(np.append(ys, ytemp[-2:1:-1]), ytemp)


        # repeat (tile) the two-way sampling depths 
        # - number of profiles we make during the survey:
        n_profiles = np.ceil(xs.size / ztwoway.size)
        zs = np.tile(ztwoway, int(n_profiles))
        zs = zs[0:xs.size]
        # sample times: (units are in seconds since zero => convert to days, to agree with ds.time)
        ts = dt * np.arange(xs.size) / 86400 

        # get rid of points with sample time > survey_time_total
        if survey_time_total > 0:
            idx = np.abs(ts*86400 - survey_time_total).argmin() # index of ts closest to survey_time_total
            print('originally, ', idx, ' points')
            # make sure this is multiple of the # of profiles:
#             idx = int(np.floor((idx+1)/n_profiles) * (n_profiles))
            idx = int(np.floor((idx+1)/len(ztwoway)) * (len(ztwoway)))
            xs = xs[:idx]
            ys = ys[:idx]
            ts = ts[:idx]
            zs = zs[:idx]
            n_profiles = np.ceil(xs.size / ztwoway.size)
            print('limited to ', idx, 'points: n_profiles=', n_profiles, ', ', len(zprofile), 'depths per profile, ', len(ztwoway), 'depths per two-way')
            
        # ---- end if not a mooring
        
    ## Assemble dataset:
    # (same regardless of sampling strategy)
    # - real (lat/lon) coordinates:
    survey_track = xr.Dataset(
        dict(
            lon = xr.DataArray(xs,dims='points'),
            lat = xr.DataArray(ys,dims='points'),
            dep = xr.DataArray(zs,dims='points'),
            time = xr.DataArray(ts,dims='points'),
            n_profiles = n_profiles
        )
    )
    # - transform to i,j,k coordinates:
    survey_indices= xr.Dataset(
        dict(
            i = xr.DataArray(f_x(survey_track.lon), dims='points'),
            j = xr.DataArray(f_y(survey_track.lat), dims='points'),
            k = xr.DataArray(f_z(survey_track.dep), dims='points'),
            time = xr.DataArray(survey_track.time, dims='points'),
        )
    )
    
    # return details about the sampling (mostly for troubleshooting)
    # could prob do this with a loop
#     sampling_parameters = {
#         'SAMPLING_STRATEGY' : SAMPLING_STRATEGY,
#         'PATTERN' : PATTERN, 
#         'zrange' : zrange,
#         'hspeed' : hspeed,
#         'vspeed' : vspeed,
#         'dt_sample' : dt
    
#     }
#     sampling_parameters = {}
    
    survey_track['SAMPLING_STRATEGY'] = SAMPLING_STRATEGY
    return survey_track, survey_indices, sampling_details
    
        
def survey_interp(ds, survey_track, survey_indices):
    """
    interpolate dataset 'ds' along the survey track given by 
    'survey_indices' (i,j,k coordinates used for the interpolation), and
    'survey_track' (lat,lon,dep,time of the survey)
    
    Returns:
        subsampled_data: all field interpolated onto the track
        sh_true: 'true' steric height along the track
    
    """
      
        
    ## Create a new dataset to contain the interpolated data, and interpolate
    # NOTE: add more metadata to this dataset
    subsampled_data = xr.Dataset() 
    
    # loop & interpolate through 3d variables:
    vbls3d = ['Theta','Salt','vorticity','steric_height']
    for vbl in vbls3d:
        subsampled_data[vbl]=ds[vbl].interp(survey_indices)
    # Interpolate U and V from i_g, j_g to i, j, then interpolate:
    # Get u, v
    grid = Grid(ds, coords={'X':{'center': 'i', 'left': 'i_g'}, 
                            'Y':{'center': 'j', 'left': 'j_g'},
                            'Z':{'center': 'k'}})
    U_c = grid.interp(ds.U, 'X', boundary='extend')
    V_c = grid.interp(ds.V, 'Y', boundary='extend')
    subsampled_data['U'] = U_c.interp(survey_indices)
    subsampled_data['V'] = V_c.interp(survey_indices)    
    
    
    # loop & interpolate through 2d variables:
    vbls2d = ['steric_height_true', 'Eta', 'KPPhbl', 'PhiBot', 'oceFWflx', 'oceQnet', 'oceQsw', 'oceSflux']
    # create 2-d survey track by removing the depth dimension
    survey_indices_2d =  survey_indices.drop_vars('k')
    for vbl in vbls2d:
        subsampled_data[vbl]=ds[vbl].interp(survey_indices_2d)   
    # taux & tauy must be treated separately, like U and V:
    oceTAUX_c = grid.interp(ds.oceTAUX, 'X', boundary='extend')
    oceTAUY_c = grid.interp(ds.oceTAUY, 'Y', boundary='extend')
    subsampled_data['oceTAUX'] = oceTAUX_c.interp(survey_indices_2d)
    subsampled_data['oceTAUY'] = oceTAUY_c.interp(survey_indices_2d)

    # add lat/lon/time to dataset
    subsampled_data['lon']=survey_track.lon
    subsampled_data['lat']=survey_track.lat
    subsampled_data['dep']=survey_track.dep
    subsampled_data['time']=survey_track.time        
          
    # steric height is technically a 3-d variable (where the depth dimension 
    # represents the deepest level from which the specific volume anomaly was interpolated)
    # - but in reality we just want the SH that was determined by integrating over
    # the full survey depth, which gives a 2-d output:
    subsampled_deepest = subsampled_data.where(subsampled_data.dep == subsampled_data.dep.min(), drop=True)
    #subsampled_data['steric_height_sampled']=subsampled_deepest 
    
    # ------Regrid the data to depth/time (3-d fields) or subsample to time (2-d fields)
    # get times associated with profiles:
    SAMPLING_STRATEGY = survey_track['SAMPLING_STRATEGY']
    if SAMPLING_STRATEGY == 'sim_mooring':
        # - for mooring, use the subsampled time grid:
        times = np.unique(subsampled_data.time.values)
    else:
        # -- for glider/uctd, take the shallowest & deepest profiles (every second value, since top/bottom get sampled twice for each profile)
        time_deepest = subsampled_data.time.where(subsampled_data.dep == subsampled_data.dep.min(), drop=True).values[0:-1:2]
        time_shallowest = subsampled_data.time.where(subsampled_data.dep == subsampled_data.dep.max(), drop=True).values[0:-1:2]
        times = np.sort(np.concatenate((time_shallowest, time_deepest)))
        # this results in a time grid that may not be uniformly spaced, but is correct
        # - for a uniform grid, use the mean time spacing - may not be perfectly accurate, but is evenly spaced
        dt = np.mean(np.diff(time_shallowest))/2 # average spacing of profiles (half of one up/down, so divide by two)
        times_uniform = np.arange(survey_track.n_profiles.values*2) * dt

    # nt is the number of profiles (times):
    nt = len(times)  
    # xgr is the vertical grid; nz is the number of depths for each profile
    zgridded = np.unique(subsampled_data.dep.data)
    nz = int(len(zgridded))

    # -- initialize the dataset:
    sgridded = xr.Dataset(
        coords = dict(depth=(["depth"],zgridded),
                  time=(["time"],times))
    )
    # -- 3-d fields: loop & reshape 3-d data from profiles to a 2-d (depth-time) grid:
    # first, extract each variable, then reshape to a grid
    for vbl in vbls3d:
        this_var = subsampled_data[vbl].data.compute().copy() 
        # reshape to nz,nt
        this_var_reshape = np.reshape(this_var,(nz,nt), order='F') # fortran order is important!
        # for platforms with up & down profiles (uCTD and glider),
        # every second column is upside-down (upcast data)
        # starting with the first column, flip the data upside down so that upcasts go from top to bottom
        if SAMPLING_STRATEGY != 'sim_mooring':
            this_var_fix = this_var_reshape.copy()
            this_var_fix[:,0::2] = this_var_fix[-1::-1,0::2] 
            sgridded[vbl] = (("depth","time"), this_var_fix)
        elif SAMPLING_STRATEGY == 'sim_mooring':
            sgridded[vbl] = (("depth","time"), this_var_reshape)
    # for sampled steric height, we want the value integrated from the deepest sampling depth:
    sgridded['steric_height'] = (("time"), sgridded['steric_height'].isel(depth=nz-1))
    # rename to "sampled" for clarity
    sgridded.rename_vars({'steric_height':'steric_height_sampled'})

    #  -- 2-d fields: loop & reshape 2-d data to the same time grid 
    vbls2d = ['steric_height_true', 'Eta', 'KPPhbl', 'PhiBot', 'oceFWflx', 'oceQnet', 'oceQsw', 'oceSflux']
    for vbl in vbls2d:
        this_var = subsampled_data[vbl].data.compute().copy() 
        # subsample to nt
        this_var_sub = this_var[0:-1:nz]
        sgridded[vbl] = (("time"), this_var_sub)



    return subsampled_data, sgridded


# great circle distance (from Jake Steinberg) 
def great_circle(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    return 6371 * (acos(sin(lat1) * sin(lat2) + cos(lat1) * cos(lat2) * cos(lon1 - lon2)))
# ## SAMPLING_STRATEGY == 'sim_mooring'; load, transpose, and convert simulated data
# # NOT WORKING
# if SAMPLING_STRATEGY == 'sim_mooring':

#     # --------- define sampling: change the values in this section -------
#     survey_time_total = ndays * 86400 # if non-zero, limits the survey to a total time
    
#     # Example: ACC_SMST mooring:
#     xmooring = 150.87
#     ymooring = -55.54
    
#     # instrument depths for T, S, and velocity
#     Tdepths = -1*np.array([120, 220, 270, 320, 370, 420, 520, 570, 620, 670, 720, 820, 895, 970, 1045, 1120, 1220, 1320, 2170, 3420, 3560]);
#     Sdepths = Tdepths 
#     UVdepths = -1*[1320, 2170, 3420, 3560]
#     ADCPdepths = np.arange(0,-1000,-10)
    
#     # sample times: (units are in seconds since zero => convert to days, to agree with ds.time)
#     ts_T = np.tile(ds.time.values,  Tdepths.size)   
#     # time resolution of sampling (dt):
#     dt = 3600 # sampling resolution in seconds
#     n_samples = ts.size    

#     # xs, ys
#     xs_T = xmooring * np.ones((Tdepths.size * n_samples))
#     ys_T = ymooring * np.ones((Tdepths.size * n_samples))
#     xs = xs_T
#     ys = ys_T
    
#     # depths: repeat (tile) the sampling depths 
#     zs_T = np.tile(Tdepths, int(n_samples))
#     zs_S = np.tile(Sdepths, int(n_samples))
#     zs_UV = np.tile(UVdepths, int(n_samples))
#     zs_ADCP = np.tile(ADCPdepths, int(n_samples))
    
        
#     ## Assemble dataset:
#     # real (lat/lon) coordinates
#     survey_track = xr.Dataset(
#         dict(
#             lon = xr.DataArray(xs_T,dims='points'),
#             lat = xr.DataArray(ys_T,dims='points'),
#             dep = xr.DataArray(zs_T,dims='points'),
#             time = xr.DataArray(ts_T,dims='points')
#         )
#     )
#     # transform to i,j,k coordinates:
#     survey_indices= xr.Dataset(
#         dict(
#             i = xr.DataArray(f_x(survey_track.lon), dims='points'),
#             j = xr.DataArray(f_y(survey_track.lat), dims='points'),
#             k = xr.DataArray(f_z(survey_track.dep), dims='points'),
#             time = xr.DataArray(survey_track.time,dims='points'),
#         )
#     )