"""Implementations of deterministic nowcasting methods."""

import time
from .. import advection

def forecast(R, V, num_timesteps, extrap_method, extrap_kwargs={}):
    """Generate a nowcast by applying a simple advection-based extrapolation to 
    the given precipitation field.
    
    Parameters
    ----------
    R : array-like
      Two-dimensional array of shape (m,n) containing the input precipitation 
      field.
    V : array-like
      Array of shape (2,m,n) containing the x- and y-components of the advection 
      field. The velocities are assumed to represent one time step.
    num_timesteps : int
      Number of time steps to forecast.
    extrap_method : str
      Name of the extrapolation method to use. See the documentation of the 
      advection module for the available choices.
    extrap_kwargs : dict
      Optional dictionary that is supplied as keyword arguments to the 
      extrapolation method.
    
    Returns
    -------
    out : ndarray
      Three-dimensional array of shape (num_timesteps,m,n) containing a time 
      series of nowcast precipitation fields.
    """
    _check_inputs(R, V)
    
    print("Computing extrapolation nowcast from a %dx%d input grid... " % \
          (R.shape[0], R.shape[1]), end="")
    
    starttime = time.time()
    
    extrap_method = advection.get_method(extrap_method)
    R_f = extrap_method(R, V, num_timesteps, **extrap_kwargs)
    
    print("%.2f seconds." % (time.time() - starttime))
    
    return R_f

def _check_inputs(R, V):
    if len(R.shape) != 2:
        raise ValueError("R must be a two-dimensional array")
    if len(V.shape) != 3:
        raise ValueError("V must be a three-dimensional array")
    if R.shape != V.shape[1:3]:
        raise ValueError("dimension mismatch between R and V: shape(R)=%s, shape(V)=%s" % \
                         (str(R.shape), str(V.shape)))
