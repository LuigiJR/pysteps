"""OpenCV implementation of the Lucas-Kanade method with interpolated motion
   vectors for areas with no precipitation
"""

import numpy as np
import cv2
import scipy
import time

def dense_lucaskanade(R, **kwargs):
    """OpenCV implementation of the Lucas-Kanade method with interpolated motion
        vectors for areas with no precipitation.

    Parameters
    ----------
    R : array-like, shape (t,m,n)
        array containing the input precipitation fields, no missing values are 
        accepted
    
    Optional kwargs
    ---------------
    max_corners_ST : int
        maximum number of corners to return. If there are more corners than are 
        found, the strongest of them is returned
    quality_level_ST : float
        parameter characterizing the minimal accepted quality of image corners.
        See original documentation for more details (https://docs.opencv.org)
    min_distance_ST : int
        minimum possible Euclidean distance between the returned corners [px]
    block_size_ST : int
        size of an average block for computing a derivative covariation matrix 
        over each pixel neighborhood
    winsize_LK : int
        size of the search window at each pyramid level. 
        Small windows (e.g. 10) lead to unrealistic motion
    nr_levels_LK : int
        0-based maximal pyramid level number.
        Not very sensitive parameter
    max_speed : float
        the maximum allowed speed [px/timestep]
    nr_IQR_outlier : int
        nr of IQR above median to consider the velocity vector as outlier and discard it
    size_opening : int
        the structuring element size for the filtering of isolated pixels [px]
    decl_grid : int
        size of the declustering grid [px]
    min_nr_samples : int
        the minimum number of samples for computing the median within given declustering cell
    function : string
        the radial basis function, based on the Euclidian norm d, used in the 
        interpolation of the sparse vectors.
        default : inverse
        available : nearest, inverse, gaussian
    k : int or "all"
        the number of nearest neighbors used to speed-up the interpolation
        If set equal to "all", it employs all the sparse vectors
        default : 20
    epsilon : float   
        adjustable constant for gaussian or inverse functions
        default : median distance between sparse vectors
    nchunks : int
        split the grid points in n chunks to limit the memory usage during the 
        interpolation
        default : 5
    extra_vectors : array-like
        additional sparse motion vectors as 2d array (columns: x,y,u,v; rows: 
        nbr. of vectors) to be integrated with the sparse vectors from the Lucas-Kanade 
        local tracking.
        x and y must be in pixel coordinates, with (0,0) being the upper-left 
        corner of the field R. u and v must be in pixel units
    verbose : bool
        if set to True, it prints information about the program
        
    Returns
    -------
    out : ndarray, shape (2,m,n)
        three-dimensional array containing the dense x- and y-components of the 
        motion field.

    """
        
    if len(R.shape) != 3:
        raise ValueError("R has %i dimensions, but a three-dimensional array is expected" % len(R.shape))
    if R.shape[0] < 2:
        raise ValueError("R has %i frame, but at least two frames are expected" % R.shape[0])
    if np.any(~np.isfinite(R)):
        raise ValueError("All values in R must be finite")
        
    # defaults
    max_corners_ST      = kwargs.get("max_corners_ST", 500)
    quality_level_ST    = kwargs.get("quality_level_ST", 0.1)
    min_distance_ST     = kwargs.get("min_distance_ST", 5)
    block_size_ST       = kwargs.get("block_size_ST", 15)
    winsize_LK          = kwargs.get("winsize_LK5", (50, 50))
    nr_levels_LK        = kwargs.get("nr_levels_LK", 2)
    max_speed           = kwargs.get("max_speed", 10)
    nr_IQR_outlier      = kwargs.get("nr_IQR_outlier", 3)
    size_opening        = kwargs.get("size_opening", 3)
    decl_grid           = kwargs.get("decl_grid", 20)
    min_nr_samples      = kwargs.get("min_nr_samples", 2)
    function            = kwargs.get("function", "inverse")
    k                   = kwargs.get("k", 20)
    epsilon             = kwargs.get("epsilon", None)
    nchunks             = kwargs.get("nchunks", 5)
    extra_vectors       = kwargs.get("extra_vectors", None)
    if extra_vectors is not None:
        if len(extra_vectors.shape) != 2:
            raise ValueError("extra_vectors has %i dimensions, but 2 dimensions are expected" 
                            % len(extra_vectors.shape))
        if extra_vectors.shape[1] != 4:
            raise ValueError("extra_vectors has %i columns, but 4 columns are expected" 
                               % extra_vectors.shape[1])
    verbose             = kwargs.get("verbose", True)
    if verbose:
        print("Computing the motion field with the Lucas-Kanade method.")
        t0 = time.time()
    
    nr_fields = R.shape[0]
    domain_size = (R.shape[1], R.shape[2])
    y0Stack=[]
    x0Stack=[]
    uStack=[]
    vStack=[]
    for n in range(nr_fields-1):

        # extract consecutive images
        prvs = R[n,:,:].copy()
        next = R[n+1,:,:].copy()

        # scale between 0 and 255
        prvs = (prvs - prvs.min())/(prvs.max() - prvs.min())*255
        next = (next - next.min())/(next.max() - next.min())*255

        # convert to 8-bit
        prvs = np.ndarray.astype(prvs,"uint8")
        next = np.ndarray.astype(next,"uint8")

        # remove small noise with a morphological operator (opening)
        prvs = clean_image(prvs, n=size_opening)
        next = clean_image(next, n=size_opening)

        # Shi-Tomasi good features to track
        # TODO: implement different feature detection algorithms (e.g. Harris)
        p0 = ShiTomasi_features_to_track(prvs, max_corners_ST, quality_level_ST,
                                          min_distance_ST, block_size_ST)
                                          
        # get sparse u, v vectors with Lucas-Kanade tracking
        x0, y0, u, v = LucasKanade_features_tracking(prvs, next, p0, winsize_LK, 
                                                     nr_levels_LK)

        # exclude outlier vectors
        speed = np.sqrt(u**2 + v**2) # [px/timesteps]
        q1, q2, q3 = np.percentile(speed, [25,50,75])
        max_speed_thr = np.min((max_speed, q2 + nr_IQR_outlier*(q3 - q1))) # [px/timesteps]
        min_speed_thr = np.max((0,q2 - 2*(q3 - q1)))
        keep = np.logical_and(speed < max_speed_thr, speed > min_speed_thr)
        
        u = u[keep][:,None]
        v = v[keep][:,None]
        y0 = y0[keep][:,None]
        x0 = x0[keep][:,None]
        
        # stack vectors within time window
        y0Stack.append(y0)
        x0Stack.append(x0)
        uStack.append(u)
        vStack.append(v)
        
    # convert lists of arrays into single arrays
    y0 = np.vstack(y0Stack)
    x0 = np.vstack(x0Stack) 
    u = np.vstack(uStack)
    v = np.vstack(vStack)
    
    # decluster sparse motion vectors
    x, y, u, v = declustering(x0, y0, u, v, decl_grid, min_nr_samples)

    # append extra vectors if provided
    if extra_vectors is not None:
        x = np.concatenate((x, extra_vectors[:, 0]))
        y = np.concatenate((y, extra_vectors[:, 1]))
        u = np.concatenate((u, extra_vectors[:, 2]))
        v = np.concatenate((v, extra_vectors[:, 3]))

    # kernel interpolation
    X, Y, UV = interpolate_sparse_vectors(x, y, u, v, domain_size, function=function,
                                          k=k, epsilon=epsilon, nchunks=nchunks)
    
    if verbose:
        print("--- %s seconds ---" % (time.time() - t0))
    
    return UV
    
def ShiTomasi_features_to_track(R, max_corners_ST, quality_level_ST,
                                 min_distance_ST, block_size_ST):
    """Call the Shi-Tomasi corner detection algorithm.

    Parameters
    ----------
    R : array-like
        Array of shape (m,n) containing the input precipitation field passed as 8-bit image.
    max_corners_ST : int
        Maximum number of corners to return. If there are more corners than are 
        found, the strongest of them is returned.
    quality_level_ST : float
        Parameter characterizing the minimal accepted quality of image corners.
        See original documentation for more details (https://docs.opencv.org).
    min_distance_ST : int
        Minimum possible Euclidean distance between the returned corners [px].
    block_size_ST : int
        Size of an average block for computing a derivative covariation matrix 
        over each pixel neighborhood. 
        
    Returns
    -------
    p0 : list
        Output vector of detected corners.
    """

    if len(R.shape) != 2:
        raise ValueError("R must be a two-dimensional array")
    if R.dtype != "uint8":
        raise ValueError("R must be passed as 8-bit image")

    # ShiTomasi corner detection parameters
    ShiTomasi_params = dict(maxCorners=max_corners_ST, qualityLevel=quality_level_ST,
                            minDistance=min_distance_ST, blockSize=block_size_ST)

    # detect corners
    p0 = cv2.goodFeaturesToTrack(R, mask=None, **ShiTomasi_params)
    
    if p0 is None:
        raise ValueError("Shi-Tomasi found no good feature to be tracked.")

    return p0

def LucasKanade_features_tracking(prvs, next, p0, winsize_LK, nr_levels_LK):
    """Call the Lucas-Kanade features tracking algorithm.

    Parameters
    ----------
    prvs : array-like
        Array of shape (m,n) containing the first 8-bit input image.
    next : array-like
        Array of shape (m,n) containing the successive 8-bit input image.
    p0 : list
        Vector of 2D points for which the flow needs to be found.
        Point coordinates must be single-precision floating-point numbers.
    winsize_LK : tuple
        Size of the search window at each pyramid level. 
        Small windows (e.g. 10) lead to unrealistic motion.
    nr_levels_LK : int
        0-based maximal pyramid level number.
        Not very sensitive parameter.
        
    Returns
    -------
    x0 : array-like
        Output vector of x-coordinates of detected point motions.
    y0 : array-like
        Output vector of y-coordinates of detected point motions.
    u : array-like
        Output vector of u-components of detected point motions.
    v : array-like
        Output vector of v-components of detected point motions.

    """

    # LK parameters
    lk_params = dict( winSize=winsize_LK, maxLevel=nr_levels_LK,
                     criteria=(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT, 10, 0))

    # Lucas-Kande
    p1, st, err = cv2.calcOpticalFlowPyrLK(prvs, next, p0, None, **lk_params)

    # keep only features that have been found
    st = st[:,0]==1
    p1 = p1[st,:,:]
    p0 = p0[st,:,:]
    err = err[st,:]

    # extract vectors
    x0 = p0[:,:,0]
    y0 = p0[:,:,1]
    u = np.array((p1-p0)[:,:,0])
    v = np.array((p1-p0)[:,:,1])

    return x0, y0, u, v
 
def clean_image(R, n=3, thr=0):
    """Apply a binary morphological opening to filter small isolated echoes.

    Parameters
    ----------
    R : array-like
        Array of shape (m,n) containing the input precipitation field.
    n : int
        The structuring element size [px].
    thr : float
        The rain/no-rain threshold to convert the image into a binary image.

    Returns
    -------
    R : array
        Array of shape (m,n) containing the cleaned precipitation field.
    """

    # convert to binary image (rain/no rain)
    field_bin = np.ndarray.astype(R > thr,"uint8")

    # build a structuring element of size (nx)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (n,n))

    # apply morphological opening (i.e. erosion then dilation)
    field_bin_out = cv2.morphologyEx(field_bin, cv2.MORPH_OPEN, kernel)

    # build mask to be applied on the original image
    mask = (field_bin - field_bin_out) > 0

    # filter out small isolated echoes based on mask
    R[mask] = np.nanmin(R)

    return R
    
def declustering(x, y, u, v, decl_grid, min_nr_samples):
    """
    Filter out outliers and get more representative data points.
    It assigns data points to a (RxR) declustering grid and then take the median of all values within one cell.
    
    Parameters
    ----------
    x0 : 
    y0 :
    u :
    v :
    decl_grid : int
        Size of the declustering grid [px].
    min_nr_samples : int
        The minimum number of samples for computing the median within given declustering cell.
        
    Returns
    -------
    x : array-like
    y : array-like
    u : array-like
    v : array-like

    """
    # make sure these are all vertical arrays
    x = x[:,None]
    y = y[:,None]
    u = u[:,None]
    v = v[:,None]
    
    # discretize coordinates into declustering grid
    xT = x/float(decl_grid)
    yT = y/float(decl_grid)
       
    # round coordinates to low integer 
    xT = np.floor(xT)
    yT = np.floor(yT)

    # keep only unique combinations of coordinates
    xy = np.hstack((xT,yT)).squeeze()
    xyb = np.ascontiguousarray(xy).view(np.dtype((np.void, xy.dtype.itemsize*xy.shape[1])))
    _,idx = np.unique(xyb, return_index=True)
    unique_xy = xy[idx]

    # now loop through these unique values and average vectors which belong to the same declustering grid cell
    xN=[]; yN=[]; uN=[]; vN=[]
    for i in range(unique_xy.shape[0]):
        idx = np.logical_and(xT==unique_xy[i,0], yT==unique_xy[i,1])
        npoints = np.sum(idx)
        if npoints >= min_nr_samples:
            xN.append(np.median(x[idx]))
            yN.append(np.median(y[idx]))
            uN.append(np.median(u[idx]))
            vN.append(np.median(v[idx]))
    
    # convert to numpy arrays
    x = np.array(xN)
    y = np.array(yN) 
    u = np.array(uN)
    v = np.array(vN) 

    return x, y, u, v
    
def interpolate_sparse_vectors(x, y, u, v, domain_size, function="inverse",
                               k=20, epsilon=None, nchunks=5):
    
    """Interpolation of sparse motion vectors to produce a dense field of motion 
    vectors. 
    
    Parameters
    ----------
    x : array-like
        x coordinates of the sparse motion vectors
    y : array-like
        y coordinates of the sparse motion vectors
    u : array_like  
        u components of the sparse motion vectors
    v : array_like  
        v components of the sparse motion vectors
    domain_size : tuple
        size of the domain of the dense motion field [px]
    function : string
        the radial basis function, based on the Euclidian norm, d.
        default : inverse
        available : nearest, inverse, gaussian
    k : int or "all"
        the number of nearest neighbors used to speed-up the interpolation
        If set equal to "all", it employs all the sparse vectors
        default : 20
    epsilon : float   
        adjustable constant for gaussian or inverse functions
        default : median distance between sparse vectors
    nchunks : int
        split the grid points in n chunks to limit the memory usage during the 
        interpolation
        default : 5
    
    Returns
    -------
    X : array-like
        grid
    Y : array-like
        grid
    UV : array-like
        Three-dimensional array (2,domain_size[0],domain_size[1]) 
        containing the dense U, V motion fields.
    """
    
    testinterpolation = False
    
    # make sure these are vertical arrays
    x = x[:,None]
    y = y[:,None]
    u = u[:,None]
    v = v[:,None]
    points = np.column_stack((x, y))
    
    if len(domain_size)==1:
        domain_size = (domain_size, domain_size)
        
    # generate the grid
    xgrid = np.arange(domain_size[1])
    ygrid = np.arange(domain_size[0])
    X, Y = np.meshgrid(xgrid, ygrid)
    grid = np.column_stack((X.ravel(), Y.ravel()))
    
    U = np.zeros(grid.shape[0])
    V = np.zeros(grid.shape[0])
         
    # create cKDTree object to represent source grid
    if k is not "all":
        k = np.min((k, points.shape[0]))
        tree = scipy.spatial.cKDTree(points)
    
    # split grid points in n chunks
    subgrids = np.array_split(grid, nchunks, 0)
    subgrids = [x for x in subgrids if x.size > 0]
    
    # loop subgrids
    i0=0
    for i,subgrid in enumerate(subgrids):
    
        idelta = subgrid.shape[0]

        if function.lower() == "nearest":
        
            # find indices of the nearest neighbors
            _, inds = tree.query(subgrid, k=1)
        
            U[i0:(i0+idelta)] = u.flatten()[inds]
            V[i0:(i0+idelta)] = v.flatten()[inds]
        
        else:
            if k == "all":
                d = scipy.spatial.distance.cdist(points, subgrid, 'euclidean').transpose()
                inds = np.arange(u.size)[None,:]*np.ones((subgrid.shape[0],u.size)).astype(int)

            else: 
                # find indices of the k-nearest neighbors
                d, inds = tree.query(subgrid, k=k)
        
            # the bandwidth
            if epsilon is None:
                dpoints = scipy.spatial.distance.pdist(points, 'euclidean')
                epsilon = np.median(dpoints)
            
            # the interpolation weights
            if function.lower() == "inverse":
                w = 1.0/np.sqrt((d/epsilon)**2 + 1)
            elif function.lower() == "gaussian":
                w = np.exp(-0.5*(d/epsilon)**2)
            else:
                raise ValueError("unknown radial fucntion %s" % function)

            U[i0:(i0+idelta)] = np.sum(w * u.flatten()[inds], axis=1) / np.sum(w, axis=1)
            V[i0:(i0+idelta)] = np.sum(w * v.flatten()[inds], axis=1) / np.sum(w, axis=1)
               
        i0 += idelta
    
    # reshape back to original size
    U = U.reshape(domain_size[0], domain_size[1])
    V = V.reshape(domain_size[0], domain_size[1])
    UV = np.stack([U, V])
        
    if testinterpolation:
        import matplotlib.pylab as plt
        step=15
        UV_ = UV[:, 0:UV.shape[1]:step, 0:UV.shape[2]:step]
        X_ = X[0:UV.shape[1]:step, 0:UV.shape[2]:step]
        Y_ = Y[0:UV.shape[1]:step, 0:UV.shape[2]:step]                                                         
        plt.quiver(X_, np.flipud(Y_), UV_[0,:,:], -UV_[1,:,:])     
        plt.quiver(x, np.flipud(y), u, -v, color="red")
        plt.show()
    
    return X, Y, UV
