""" 
network.py
The network class used in the draculab simulator.
"""

from draculab import unit_types, synapse_types, plant_models, syn_reqs  # names of models and requirements
import numpy as np
from cython_utils import * # interpolation and integration methods including cython_get_act*,
#from requirements import *  # not sure this is necessary
from array import array # optionally used for the unit's buffer
#from numba import jit
#import dill
#from pathos.multiprocessing import ProcessingPool

def upd_unit(u, t):
    """ Runs a unit's update function.
    
        Auxiliary to flat_update in order to parallelize updates. 
    
        Args:
            u : a unit object
            t : a time
        Returns
            u : the same unit
    """
    if hasattr(u, 'buffer'):
        if u.multiport and u.needs_mp_inp_sum:
            u.upd_flat_mp_inp_sum(time)
        else:
            u.upd_flat_inp_sum(time)
    return u        

class network():
    """ 
    This class has the tools to build and simulate a network.

    Roughly, the steps are:
    First, create an instance of network(); 
    second, use the create() method to add units and plants;
    third, use source.set_function() for source units, which provide inputs;
    fourth, use the connect() method to connect the units, 
    fifth, use set_plant_inputs() and set_plant_outputs() to connect the plants;
    finally use the run() method to run the simulation.

    More information is provided by the tutorials.
    """

    def __init__(self, params):
        """
        The network class constructor.

        Args:
            params : parameter dictionary
            REQUIRED PARAMETERS
                min_delay : minimum transmission delay, and simulation step size.
                min_buff_size : number of network states to store for each simulation step.
            OPTIONAL PARAMETERS 
                rtol = relative tolerance in the ODE integrator.
                atol = absolute tolerance in the ODE integrator.
                See: https://docs.scipy.org/doc/scipy/reference/generated/scipy.integrate.odeint.html
        """
        self.sim_time = 0.0  # current simulation time [ms]
        self.n_units = 0     # current number of units in the network
        self.units = []      # list with all the unit objects
        self.n_plants = 0    # current number of plants in the network
        self.plants = []     # list with all the plant objects
        # The next 3 lists implement the connectivity of the network
        self.delays = [] # delays[i][j] is the delay of the j-th connection to unit i 
        self.act = []    # act[i][j] is the function from which unit i obtains its j-th input
        self.syns = []   # syns[i][j] is the synapse object for the j-th connection to unit i
        self.min_delay = params['min_delay'] # minimum transmission delay
        self.min_buff_size = int(params['min_buff_size'])  # number of values stored during
                                                           # a minimum delay period
        if 'rtol' in params: self.rtol = params['rtol']
        else: self.rtol = 1e-6 # relative tolerance of the integrator
        if 'atol' in params: self.atol = params['atol']
        else: self.atol = 1e-6 # absolute tolerance of the integrator
        self.flat = False # This network has not been "flattened"
        

    def create(self, n, params):
        """
        This method is a wrapper to methods that create units or plants.

        If we're creating units, it will call create_units(n, params).
        If we're creating a plant, it will call create_plant(n, params).

        Raises:
            TypeError.
        """
        if hasattr(unit_types, params['type'].name):
            return self.create_units(n,params)
        elif hasattr(plant_models, params['type'].name):
            return self.create_plant(n, params)
        else:
            raise TypeError('Tried to create an object of an unknown type')
        if self.flat:
            raise AssertionError("Adding elements to a flattened network")


    def create_plant(self, n, params):
        """
        Create a plant with model params['type']. 
        
        The current implementation only creates one plant per call, so n != 1 will 
        raise an exception.  The method returns the ID of the created plant.

        Args:
            n: the integer 1.
            params: a dictionary of parameters used for plant creation.
                REQUIRED PARAMETERS
                'type': a model from the plant_models enum.
                Other required parameters depend on the specific plant model.

        Returns: 
            An integer with the ID of the created plant.

        Raises:
            AssertionError, NotImplementedError, ValueError.
        """
        assert self.sim_time == 0., 'A plant is being created when the ' + \
                                    'simulation time is not zero'
        if n != 1:
            raise ValueError('Only one plant can be created on each call to create()')
        plantID = self.n_plants
        try:
            plant_class = params['type'].get_class()
        except NotImplementedError: # raising the same exception with a different message
            raise NotImplementedError('Attempting to create a plant with ' +
                                      'an unknown model type')

        self.plants.append(plant_class(plantID, params, self))
        self.n_plants += 1
        return plantID
   

    def create_units(self, n, params):
        """
        create 'n' units of type 'params['type']' and parameters from 'params'.

        The method returns a list with the ID's of the created units.
        If you want one of the parameters to have different values for each unit, 
        you can have a list (or numpy array) of length 'n' in the corresponding 
        'params' entry

        In addition, this function can give a particular spatial arrangement to the 
        created units by appropriately setting their 'coordinates' attribute.

        Args:
            n: an integer indicating how many units to create.
            params: a dictionary with the parameters used to initialize the units.
                REQUIRED PARAMETERS
                type: a unit model form the unit_types enum.
                init_val: initial activation value (also required for source units).
                OPTIONAL PARAMETERS
                coordinates: The spatial location of the units can be specified in 2 ways:
                                * One numpy array (a single point). 
                                  All units will have this location.
                                * A list of n arrays. Each unit will be assigned one array.
                                          
                For other required and optional parameters, consult the specific unit models.
        Returns: a list with the ID's of the created units.
        Raises:
            ValueError, TypeError, NotImplementedError
                
        """
        assert (type(n) == int) and (n > 0), 'Number of units must be a ' + \
                                             'positive integer'
        assert self.sim_time == 0., 'Units are being created when the ' + \
                                    'simulation time is not zero'

        # Entries in 'params' are expected to be either be a scalar,
        # a boolean, a list of length 'n', or a numpy array of length 'n'. 
        # The following parameters are particular exceptions:
        # 'coordinates', 'type', 'function', 'branch_params', 'integ_meth',
        # 'init_val', and 'extra_requirementes'.
        listed = [] # the entries in 'params' specified with a list
        accepted_types = [float, int, np.float_, np.int_, bool, str] # accepted data types
        for par in params:
            if par == 'type':
                if not issubclass(type(params[par]), unit_types):
                    raise TypeError('Incorrect unit type')
            elif par == 'coordinates':
            # 'coordinates' should be either a list (with 'n' arrays) or a (1|2|3) array.
                if type(params[par]) is np.ndarray:
                    pass
                elif type(params[par]) is list:
                    if len(params[par]) == n:
                        listed.append(par)
                    else:
                        raise ValueError('coordinates list has incorrect size')
                else:
                    raise TypeError('Incorrect type for the coordinates parameter')
            elif par == 'function':
                if type(params[par]) is list:
                    if len(params[par]) == n:
                        listed.append(par)
                elif not callable(params[par]):
                    raise TypeError('Incorrect function initialization in create_units')
            elif par == 'branch_params': # used by the double_sigma family of units
                if type(params[par]) is dict:
                    pass
                elif type(params[par]) is list:
                    if len(params[par]) == n:
                        listed.append(par)
                    else:
                        raise ValueError('branch_params list has incorrect size')
            elif par == 'integ_meth':
                if type(params[par]) is list:
                    if len(params[par]) == n:
                        listed.append(par)
                elif not type(params[par]) is str:
                    raise TypeError('Invalid type given for the integ_meth parameter')
            elif par == 'extra_requirements':
                if type(params[par]) is list:
                    req_names = syn_reqs.list_names()
                    for req in params[par]:
                        if not req in req_names:
                            raise ValueError('Attempting to create a unit with ' +
                                             'an unknown extra_requirement')
                else:
                    raise ValueError('The extra_requirements parameter should'+
                                     ' be a list of requirement names.')
            elif par == 'init_val' and 'multidim' in params and params['multidim'] is True:
            # 'init_val' can be a scalar, a list(array) or a list(array) of lists(arrays);
            # this presents a possible ambiguity when it is a list, because it could either be
            # the initial state of an n-dimensional model, or it could be the n scalar
            # initial values of a scalar model. Thus we rely on unit.multidim .
                if type(params[par][0]) in [list, np.ndarray]:
                # We have a list of lists (or ndarray of ndarrays, etc.)
                    if len(params[par]) == n:
                        listed.append(par)
                    else:
                        raise ValueError('list of multidimensional init_val ' +
                                         'entries has incorrect number of elements')
                elif not type(params[par][0]) in [float, int, np.float_, np.int_]:
                    raise TypeError('Incorrect type used in multidimensional init_val')
            elif (type(params[par]) is list) or (type(params[par]) is np.ndarray):
                if len(params[par]) == n:
                    listed.append(par)
                else:
                    raise ValueError('Found parameter list of incorrect size ' +
                                     'during unit creation')
            elif not (type(params[par]) in accepted_types):
                raise TypeError('Found parameter "' + par + '" with the wrong ' +
                                'type during unit creation')
                    
        params_copy = params.copy() # The 'params' dictionary that a unit receives 
                             # in its constructorshould only contain scalar values. 
                             # params_copy won't have lists
        # Creating the units
        unit_list = list(range(self.n_units, self.n_units + n))
        try: 
            unit_class = params['type'].get_class()
        except NotImplementedError:
            raise NotImplementedError('Attempting to create a unit with an unknown type')

        for ID in unit_list:
            for par in listed:
                params_copy[par] = params[par][ID-self.n_units]
            self.units.append(unit_class(ID, params_copy, self))

        self.n_units += n
        # putting n new slots in the delays, act, and syns lists
        self.delays += [[] for i in range(n)]
        self.act += [[] for i in range(n)]
        self.syns += [[] for i in range(n)]
        # note:  [[]]*n causes all empty lists to be the same object 
        # running init_pre_syn_update for the new units
        for unit in [self.units[idx] for idx in unit_list]:
            unit.init_pre_syn_update()

        return unit_list


    def connect(self, from_list, to_list, conn_spec, syn_spec):
        """
        Connect units using delayed transmission lines and synaptic connections.

        connect the units in the 'from_list' to the units in the 'to_list' using the
        connection specifications in the 'conn_spec' dictionary, and the
        synapse specfications in the 'syn_spec' dictionary.

        Args:
            from_list: A list with the IDs of the units sending the connections

            to_list: A list the IDs of the units receiving the connections
            
            conn_spec: A dictionary specifying a connection rule, and delays.
                REQUIRED PARAMETERS
                'rule' : a string specifying a rule on how to create the connections. 
                        Currently implemented: 
                        'fixed_outdegree' - an 'outdegree' integer entry must also be in conn_spec.
                        'fixed_indegree' - an 'indegree' integer entry must also be in conn_spec.
                        'one_to_one' - from_list and to_list should have the same length.
                        'all_to_all'.
                'delay' : either a dictionary specifying a distribution, a scalar delay value that
                        will be applied to all connections, or a list with a delay value for each one
                        of the connections to be made. Implemented distributions:
                        'uniform' - the delay dictionary must also include 'low' and 'high' values.
                            Example:
                            {...,'delay':{'distribution':'uniform', 'low':0.1, 'high':0.3}, ...}
                        Delays should be multiples of the network minimum delay.
                OPTIONAL PARAMETERS
                'allow_autapses' : True or False. Can units connect to themselves? Default is True.
                'allow_multapses' : Can units send many connections to another unit?  Default is False.

            syn_spec: A dictionary used to initialize the synapses in the connections.
                REQUIRED PARAMETERS
                'type' : a synapse type from the synapse_types enum.
                'init_w' : Initial weight values. Either a dictionary specifying a distribution, a
                        scalar value to be applied for all created synapses, or a list with length equal
                        to the number of connections to be made. Distributions:
                        'uniform' : the delay dictionary must also include 'low' and 'high' values.
                        Example: {..., 'init_w':{'distribution':'uniform', 'low':0.1, 'high':1.} }
                        'equal_norm' : the weight vectors for units in 'to_list' are uniformly sampled
                                      from the space of vectors with a given norm. The init_w 
                                      dictionary must also include the 'norm' parameter.
                        Example: {..., 'init_w':{'distribution':'equal_norm', 'norm':1.5} }
                        If using a list to specify the initial weights, the first entries in the
                        list correspond to the connections from unit 'from_list[0]', the following
                        to the connections from unit 'from_list[1]', and so on.
                Any other required parameters (e.g. 'lrate') depend on the synapse type.
                OPTIONAL PARAMETERS
                'inp_ports' : input ports of the connections. Either a single integer, or a list.
                            If using a list, its length must match the number of connections being
                            created, which depends on the conection rule. The first entries in the
                            list correspond to the connections from unit 'from_list[0]', the following
                            to the connections from unit 'from_list[1]', and so on. In practice it is
                            not recommended to use many input ports in a single call to 'connect'.
                    
        Raises:
            ValueError, TypeError, NotImplementedError.
        """
        
        # A quick test first (all unit ID's are in the right range)
        if ((np.amax(from_list + to_list) > self.n_units-1) or 
            (np.amin(from_list + to_list) < 0)):
            raise ValueError('Attempting to connect units with an ID out of range')
        # Ensuring the network hasn't been flattened
        if self.flat:
            raise AssertionError("Adding connections to a flattened network")

        # Retrieve the synapse class from its type object
        syn_class = syn_spec['type'].get_class()

        # If 'allow_autapses' not in dictionary, set default value
        if not ('allow_autapses' in conn_spec): conn_spec['allow_autapses'] = True
        # If 'allow_multapses' not in dictionary, set default value
        if not ('allow_multapses' in conn_spec): conn_spec['allow_multapses'] = False
       
        # The units connected depend on the connectivity rule in conn_spec
        # We'll specify  connectivity by creating a list of 2-tuples with all the
        # pairs of units to connect
        connections = []  # the list with all the connection pairs as (source,target)

        if conn_spec['allow_multapses']:
            rep = True  # sampling will be done with replacement
        else:
            rep = False

        if conn_spec['rule'] == 'fixed_outdegree':  #<----------------------
            assert len(to_list) >= conn_spec['outdegree'] or rep, ['Outdegree larger ' +
                                             'than number of targets']
            for u in from_list:
                if conn_spec['allow_autapses']:
                    #targets = random.sample(to_list, conn_spec['outdegree'])
                    targets = np.random.choice(to_list, size=conn_spec['outdegree'], 
                                               replace=rep)
                else:
                    to_copy = to_list.copy()
                    while u in to_copy:
                        to_copy.remove(u)
                    #targets = random.sample(to_copy, conn_spec['outdegree'])
                    targets = np.random.choice(to_copy, size=conn_spec['outdegree'], 
                                               replace=rep)
                connections += [(u,y) for y in targets]
        elif conn_spec['rule'] == 'fixed_indegree':   #<----------------------
            assert len(from_list) >= conn_spec['indegree'] or rep, ['Indegree larger ' +
                                                              'than number of sources']
            for u in to_list:
                if conn_spec['allow_autapses']:
                    #sources = random.sample(from_list, conn_spec['indegree'])
                    sources = np.random.choice(from_list, size=conn_spec['indegree'],
                                               replace=rep)
                else:
                    from_copy = from_list.copy()
                    while u in from_copy:
                        from_copy.remove(u)
                    #sources = random.sample(from_copy, conn_spec['indegree'])
                    sources = np.random.choice(from_copy, size=conn_spec['indegree'],
                                               replace=rep)
                connections += [(x,u) for x in sources]
        elif conn_spec['rule'] == 'all_to_all':    #<----------------------
            targets = to_list
            sources = from_list
            if conn_spec['allow_autapses']:
                connections = [(x,y) for x in sources for y in targets]
            else:
                connections = [(x,y) for x in sources for y in targets if x != y]
        elif conn_spec['rule'] == 'one_to_one':   #<----------------------
            if len(to_list) != len(from_list):
                raise ValueError('one_to_one connectivity requires equal number ' +
                                 'of sources and targets')
            connections = list(zip(from_list, to_list))
            if conn_spec['allow_autapses'] == False:
                connections = [(x,y) for x,y in connections if x != y]
        else:
            raise ValueError('Attempting connect with an unknown rule')
            
        n_conns = len(connections)  # number of connections we'll make

        # Initialize the weights. We'll create a list called 'weights' that
        # has a weight for each entry in 'connections'
        if type(syn_spec['init_w']) is dict: 
            w_dict = syn_spec['init_w']
            if w_dict['distribution'] == 'uniform':  #<----------------------
                weights = np.random.uniform(w_dict['low'], w_dict['high'], n_conns)
            elif w_dict['distribution'] == 'equal_norm':  #<----------------------
                # For each unit in 'to_list', get the indexes where it appears 
                # in 'connections', create a vector with the given norm, and 
                # distribute it with those indexes in the 'weights' vector
                weights = np.zeros(len(connections)) # initializing 
                for unit in to_list:  # For each unit in 'to_list'
                    idx_list = []
                    for idx,conn in enumerate(connections): # get indexes where unit appears
                        if conn[1] == unit:
                            idx_list.append(idx)
                    if len(idx_list) > 0:  # create the vector, 
                                           # set it in 'weights' using the indexes
                        norm_vec = np.random.uniform(0.,1.,len(idx_list))
                        norm_vec = (w_dict['norm'] / np.linalg.norm(norm_vec)) * norm_vec
                        for id_u, id_w in enumerate(idx_list):
                            weights[id_w] = norm_vec[id_u]
            else:
                raise NotImplementedError('Initializing weights with an unknown distribution')
        elif type(syn_spec['init_w']) in [float, int, np.float_, np.int_]:
            weights = [float(syn_spec['init_w'])] * n_conns
        elif type(syn_spec['init_w']) is list or type(syn_spec['init_w']) is np.ndarray:
            if len(syn_spec['init_w']) == n_conns:
                weights = syn_spec['init_w']
            else:
                raise ValueError('Received wrong number of weights to initialize connections')
        else:
            raise TypeError('The value given to the initial weights is of the wrong type')

        # Initialize the delays. We'll create a list 'delayz' that
        # has a delay value for each entry in 'connections'
        if type(conn_spec['delay']) is dict: 
            d_dict = conn_spec['delay']
            if d_dict['distribution'] == 'uniform':  #<----------------------
                # delays must be multiples of the minimum delay
                low_int = max(1, round(d_dict['low']/self.min_delay))
                high_int = max(1, round(d_dict['high']/self.min_delay)) + 1 #+1, so randint can choose it
                delayz = np.random.randint(low_int, high_int,  n_conns)
                delayz = self.min_delay * delayz
            else:
                raise NotImplementedError('Initializing delays with an unknown distribution')
        elif type(conn_spec['delay']) is float or type(conn_spec['delay']) is int:
            if (conn_spec['delay']+1e-6)%self.min_delay < 2e-6:
                delayz = [float(conn_spec['delay'])] * n_conns
            else:
                raise ValueError('Delays should be multiples of the network minimum delay')
        elif type(conn_spec['delay']) is list:
            if len(conn_spec['delay']) == n_conns:
                delayz = conn_spec['delay']
                for dely in delayz:
                    if (dely+1e-6)%self.min_delay > 2e-6:
                        raise ValueError('Delays should be multiples of the network minimum delay')
            else:
                raise ValueError('Received wrong number of delays to initialize connections')
        else:
            raise TypeError('The value given to the delay is of the wrong type')

        # Initialize the input ports, if specified in the syn_spec dictionary
        if 'inp_ports' in syn_spec:
            if type(syn_spec['inp_ports']) is int:
                portz = [syn_spec['inp_ports']]*n_conns
            elif type(syn_spec['inp_ports']) is list:
                if len(syn_spec['inp_ports']) == n_conns:
                    portz = syn_spec['inp_ports']
                else:
                    print(syn_spec['inp_ports'])
                    print(n_conns)
                    raise ValueError('Number of input ports specified does not ' +
                                     'match number of connections created')
            else:
                raise TypeError('Input ports were specified with the wrong data type')
        else:
            portz = [0]*n_conns

        # To specify connectivity, you need to update 3 lists: delays, act, and syns
        # Using 'connections', 'weights', 'delayz', and 'portz' this is straightforward
        for idx, (source,target) in enumerate(connections):
            # specify that 'target' neuron has the 'source' input
            self.act[target].append(self.units[source].get_act)
            # add a new synapse object for our connection
            syn_params = syn_spec.copy() # a copy of syn_spec just for this connection
            syn_params['preID'] = source
            syn_params['postID'] = target
            syn_params['init_w'] = weights[idx]
            syn_params['inp_port'] = portz[idx]
            syn_params['syns_loc'] = len(self.syns[target]) # location in syns[postID]
            self.syns[target].append(syn_class(syn_params, self))
            # specify the delay of the connection
            self.delays[target].append( delayz[idx] )
            if self.units[source].delay <= delayz[idx]: # this is the longest delay for this source
                self.units[source].delay = delayz[idx]+self.min_delay
                # added self.min_delay because the ODE solver may ask for values out of range,
                # depending on the order in which the units are updated (e.g. when a unit asks
                # for activation values to a units that has already updated, those values will
                # be 'min_delay' too old for the updated unit).
                # After changing the delay we need to init_buffers again. Done below.

        # After connecting, run init_pre_syn_update and init_buffers for all the units connected 
        connected = [x for x,y in connections] + [y for x,y in connections]
        for u in set(connected):
            self.units[u].init_pre_syn_update()
            self.units[u].init_buffers() # this should go second, so it uses the new syn_needs


    def set_plant_inputs(self, unitIDs, plantID, conn_spec, syn_spec):
        """ Set the activity of some units as the inputs to a plant.

            Args:
                unitIDs: a list with the IDs of the input units
                plantID: ID of the plant that will receive the inputs
                conn_spec: a dictionary with the connection specifications
                    REQUIRED ENTRIES
                    'inp_ports' : Either an integer or a list. If an integer, it
                                  will be the input port for all inputs. If a list,
                                  the i-th entry determines the input type of
                                  the i-th element in the unitIDs list.
                    'delays' : Delay value for the inputs. A scalar, or a list of 
                               length len(unitIDs).
                               Delays should be multiples of the network minimum delay.
                syn_spec: a dictionary with the synapse specifications.
                    REQUIRED ENTRIES
                    'type' : one of the synapse_types. 
                             Currently only 'static' allowed, because the
                             plant does not update the synapse dynamics in its 
                             update method.
                    'init_w': initial synaptic weight. A scalar, 
                              a list of length len(unitIDs), or
                              a dictionary specifying a distribution. 
                              Distributions:
                              'uniform' - the delay dictionary must also include 
                              'low' and 'high' values.
                              Example: 
                              {...,'init_w':{'distribution':'uniform', 'low':0.1, 'high':1.}}
            Raises:
                ValueError, NotImplementedError

        """
        # First check that the IDs are inside the right range
        if (np.amax(unitIDs) >= self.n_units) or (np.amin(unitIDs) < 0):
            raise ValueError('Attempting to connect units with an ID out of range')
        if (plantID >= self.n_plants) or (plantID < 0):
            raise ValueError('Attempting to connect to a plant with an ID out of range')

        # Then connect them to the plant...
        # Have to create a list with the delays (if one is not given in the conn_spec)
        if type(conn_spec['delays']) is float:
            delys = [conn_spec['delays'] for _ in unitIDs]
        elif (type(conn_spec['delays']) is list) or (type(conn_spec['delays']) is np.ndarray):
            delys = conn_spec['delays']
        else:
            raise ValueError('Invalid value for delays when connecting units to plant')
        # Have to create a list with all the synaptic weights
        static_synapse = synapse_types.static.get_class()
        if syn_spec['type'] is synapse_types.static: 
            synaps = []
            syn_spec['postID'] = plantID
            if type(syn_spec['init_w']) is float:
                weights = [syn_spec['init_w']]*len(unitIDs)
            elif ((type(syn_spec['init_w']) is list) or 
                  (type(syn_spec['init_w']) is np.ndarray)):
                weights = syn_spec['init_w']
            elif type(syn_spec['init_w']) is dict: 
                w_dict = syn_spec['init_w']
                if w_dict['distribution'] == 'uniform':  #<----------------------
                    weights = np.random.uniform(w_dict['low'], w_dict['high'], len(unitIDs))
                else:
                    raise NotImplementedError('Initializing weights with an ' +
                                              'unknown distribution')
            else:
                raise ValueError('Invalid value for initial weights when ' +
                                 'connecting units to plant')

            for pre,w in zip(unitIDs,weights):
                syn_spec['preID'] = pre
                syn_spec['init_w'] = w
                synaps.append( static_synapse(syn_spec, self) )
        else:
            raise NotImplementedError('Inputs to plants only use static synapses')

        inp_funcs = [self.units[uid].get_act for uid in unitIDs]
        ports = conn_spec['inp_ports'] 
        # Now just use this auxiliary function in the plant class
        self.plants[plantID].append_inputs(inp_funcs, ports, delys, synaps)

        # You may need to update the delay of some sending units
        for dely, unit in zip(delys, [self.units[ID] for ID in unitIDs]):
            if dely >= unit.delay: # This is the longest delay for the sending unit
                unit.delay = dely + self.min_delay
                unit.init_buffers()

        # run init_pre_syn_update and init_buffers for all the units connected 
        for u in unitIDs:
            self.units[u].init_pre_syn_update()
            self.units[u].init_buffers() # this should go second, to use new syn_needs

   
    def set_plant_outputs(self, plantID, unitIDs, conn_spec, syn_spec):
        """ Connect the outputs of a plant to the units in a list.

        Args:
            plantID: ID of the plant sending the outpus (an integer).

            unitIDs: a list with the IDs of the units receiving inputs from the plant.

            conn_spec: a dictionary with the connection specifications.
                REQUIRED ENTRIES
                'port_map': a list used to specify which output of the plant goes 
                            to which input port in each of the units. There are two 
                            options for this; one uses the same output-to-port map 
                            for all units, and one specifies it separately
                            for each individual unit. More precisely, the two options are:
                            1) port_map is a list of 2-tuples (a,b), indicating that 
                               output 'a' of the plant (the a-th element in the state 
                               vector) connects to port 'b'. This port connectivity 
                               scheme will be applied to all units.
                            2) port_map[i] is a list of 2-tuples (a,b), indicating that
                               output 'a' of the plant is connected to port 'b' for the
                               i-th neuron in the neuronIDs list.
                            For example if unitIDs has two elements:
                               [(0,0),(1,1)] -> output 0 to port 0 and 
                                                output 1 to port 1 for the 2 units.
                                [ [(0,0),(1,1)], [(0,1)] ] -> Same as above for the first
                                                unit, map 0 to 1 for the second unit. 
                'delays' : either a dictionary specifying a distribution, 
                           a scalar delay value that will be applied to all connections, 
                           or a list of values. 
                           Implemented dsitributions:
                           'uniform' - the delay dictionary must also include 
                                       'low' and 'high' values.
                                Example:  
                                'delays':{'distribution':'uniform', 'low':0.1, 'high':1.} 
                            Delays should be multiples of the network minimum delay.

            syn_spec: a dictionary with the synapse specifications.
                REQUIRED ENTRIES
                'type' : one of the synapse_types. The plant parent class does not 
                         currently store and update values used for synaptic plasticity, 
                         so synapse models that require presynaptic values 
                         (e.g. lpf_fast) will lead to errors.
                'init_w': initial synaptic weight. A scalar, or a list of length len(unitIDs)

        Raises:
            ValueError, TypeError.
        """
        # There is some code duplication with connect, when setting weights and delays, 
        # but it's not quite the same.

        # Some utility functions
        # this function gets a list, returns True if all elements are tuples
        T_if_tup = lambda x : (True if (len(x) == 1 and type(x[0]) is tuple) else 
                               (type(x[-1]) is tuple) and T_if_tup(x[:-1]) )
        # this function gets a list, returns True if all elements are lists
        T_if_lis = lambda x : (True if (len(x) == 1 and type(x[0]) is list) else 
                               (type(x[-1]) is list) and T_if_lis(x[:-1]) )
        # this function returns true if its argument is float or int
        foi = lambda x : True if type(x) in [float, int, np.float_, np.int_] else False
        # this function gets a list, returns True if all elements are float or int
        T_if_scal = lambda x : ( True if (len(x) == 1 and foi(x[0])) else 
                                 foi(x[-1])  and T_if_scal(x[:-1]) )

        # First check that the IDs are inside the right range
        if (np.amax(unitIDs) >= self.n_units) or (np.amin(unitIDs) < 0):
            raise ValueError('Attempting to connect units with an ID out of range')
        if (plantID >= self.n_plants) or (plantID < 0):
            raise ValueError('Attempting to connect to a plant with an ID out of range')
       
        # Retrieve the synapse class from its type object
        syn_class = syn_spec['type'].get_class()

        # Now we create a list with all the connections. In this case, each connection is
        # described by a 3-tuple (a,b,c). a=plant's output port. b=ID of receiving unit.
        # c=input port of receiving unit.
        pm = conn_spec['port_map']
        connections = []
        if T_if_tup(pm): # one single list of tuples
            for uid in unitIDs: 
                for tup in pm:
                    connections.append((tup[0], uid, tup[1]))
        elif T_if_lis(pm): # a list of lists of tuples
            if len(pm) == len(unitIDs):
                for uid, lis in zip(unitIDs, pm):
                    if T_if_tup(lis):
                        for tup in lis:
                            connections.append((tup[0], uid, tup[1]))
                    else:
                        raise ValueError('Incorrect port map format for unit ' + str(uid))
            else:
                raise ValueError('Wrong number of entries in port map list')
        else:
            raise TypeError('port map specification should be a list of tuples, ' + \
                             'or a list of lists of tuples')
            
        n_conns = len(connections)

        # Initialize the weights. We'll create a list called 'weights' that
        # has a weight for each entry in 'connections'
        if type(syn_spec['init_w']) is float or type(syn_spec['init_w']) is int:
            weights = [float(syn_spec['init_w'])] * n_conns
        elif type(syn_spec['init_w']) is list and T_if_scal(syn_spec['init_w']):
            if len(syn_spec['init_w']) == n_conns:
                weights = syn_spec['init_w']
            else:
                raise ValueError('Number of initial weights does not match ' + \
                            'number of connections being created:'+str(n_conns))
        else:
            raise TypeError('The value given to the initial weights is of the wrong type')

        # Initialize the delays. We'll create a list 'delayz' that
        # has a delay value for each entry in 'connections'
        if type(conn_spec['delays']) is dict: 
            d_dict = conn_spec['delays']
            if d_dict['distribution'] == 'uniform':  #<----------------------
                # delays must be multiples of the minimum delay
                low_int = max(1, round(d_dict['low']/self.min_delay))
                high_int = max(1, round(d_dict['high']/self.min_delay)) + 1 # +1, 
                                                       # so randint can choose it
                delayz = np.random.randint(low_int, high_int,  n_conns)
                delayz = self.min_delay * delayz
            else:
                raise NotImplementedError('Initializing delays with an ' +
                                          'unknown distribution')
        elif type(conn_spec['delays']) is float or type(conn_spec['delays']) is int:
            delayz = [float(conn_spec['delays'])] * n_conns
        elif type(conn_spec['delays']) is list and T_if_scal(conn_spec['delays']):
            if len(conn_spec['delays']) == n_conns:
                delayz = conn_spec['delays']
            else:
                raise ValueError('Number of delays does not match the number ' +
                                 'of connections being created')
        else:
            raise TypeError('The value given to the delay is of the wrong type')

        # To specify connectivity, you need to update 3 lists: delays, act, and syns
        # Using 'connections', 'weights', and 'delayz', this is straightforward
        for idx, (output, target, port) in enumerate(connections):
            # specify that 'target' neuron has the 'output' input
            self.act[target].append(self.plants[plantID].get_state_var_fun(output))
            # add a new synapse object for our connection
            syn_params = syn_spec.copy() # a copy of syn_spec just for this connection
            syn_params['preID'] = plantID
            syn_params['postID'] = target
            syn_params['init_w'] = weights[idx]
            syn_params['inp_port'] = port
            syn_params['plant_out'] = output
            syn_params['plant_id'] = plantID
            self.syns[target].append(syn_class(syn_params, self))

            # specify the delay of the connection
            if (delayz[idx]+1e-6)%self.min_delay < 2e-6:
                self.delays[target].append( delayz[idx] )
            else:
                raise ValueError('Delays should be multiples of the network minimum delay')
            if self.plants[plantID].delay <= delayz[idx]: # this is the longest
                                                        # delay for this source
                # add self.min_delay because the ODE solver may 
                # ask for out of range values
                self.plants[plantID].delay = delayz[idx]+self.min_delay
                self.plants[plantID].init_buffers() # update plant buffers
                
        # After connecting, run init_pre_syn_update and init_buffers for all the units connected 
        connected = [y for x,y,z in connections] 
        for u in set(connected):
            self.units[u].init_pre_syn_update()
            self.units[u].init_buffers() # this should go second, so it uses the new syn_needs


    def flatten(self):
        """ Move the buffers into the network object. 
        
            The unit and plant buffers will be placed in a single 2D numpy array called
            'acts' in the network object, but each unit will retain a buffer that is a
            view of part of one or more rows in 'acts'.

            The 'times' array of all units is also replaced by a sngle 'ts' array.

            After this method is called, the network can only be run with the
            'flat_run' method. Flattening a network should only be done after the
            network is fully built.
        """
        if self.sim_time > 0.:  # the network has been run before
            raise AssertionError("The network should not be flattened after simulating") 
        if self.flat:
            from warnings import warn
            warn('Network is being flattened more than once', UserWarning)
            return

        # obtain the maximum delay from all unit projections
        max0 = lambda x: 0 if len(x)==0 else max(x)  # auxiliary function
        if len(self.delays) > 0:
            max_u_del = max([max0(l) for l in self.delays])        
        else:
            max_u_del = self.min_delay
        # obtain the maximum delay from the units 'delay' attribute
        # This is needed because units can have buffers that are larger
        # than strictly needed by their transmission delays
        for u in self.units:
            if u.delay > max_u_del:
                max_u_del = u.delay
        # obtain the maximum delay from all connections to plants
        max_p_del = self.min_delay # maximum delay of connections to plants
        for p in self.plants:
            max_p_del = max(max_p_del, max0([max0(dl) for dl in p.inp_dels]))
        self.max_del = max(max_u_del, max_p_del) + self.min_delay #  min_delay added
                                                                  # (see 'connect')
        """
        # Obtain the largest buffer width among all units.
        max_buff_wid = 0
        for u in self.units:
            if hasattr(u, 'buffer') and u.buffer.shape[-1] > max_buff_wid:
                max_buff_wid = u.buffer.shape[-1]
        """
        # initialize the ts array (called 'times' in units)
        self.bf_type = np.float64  # data type of the buffers when using numpy arrays
        max_steps = int(round(self.max_del/self.min_delay)) # max number of min_del 
                                                            # steps in all delays
        self.ts_buff_size = int(round(max_steps*self.min_buff_size)) # number of 
                                                    # activation values to store
        #self.ts_buff_size = max(self.ts_buff_size, max_buff_wid) # in case we have
                                                  # units with "oversized" buffers
        self.ts_bit = self.min_delay / self.min_buff_size # time between buffer values
        self.ts = np.linspace(-self.max_del+self.ts_bit, 0.,
                               self.ts_buff_size, dtype=self.bf_type) 
                               # the corresponding times for the buffer values
        self.ts_grid = np.linspace(0., self.min_delay, self.min_buff_size+1,
                                   dtype=self.bf_type) # used to create values for 
                                                       # 'times' (optionally)
        # copy info about the unit buffers into the network object
        self.has_buffer = [False] * self.n_units # does the unit have a buffer?
        self.init_ts_idx = [0] * self.n_units # first index of ts to consider for each unit 
        self.buff_len = [0] * self.n_units # length of buffer for each unit
        self.first_idx = [0] * self.n_units # first_idx[i] indicates the row of the acts
                                            # array with the first state variable (by
                                            # convention the activity) for the i-th unit
        n_u_vars = 0 # auxiliary variable to fill self.first_idx
        for uid, u in enumerate(self.units):
            if hasattr(u, 'buffer'):
                self.has_buffer[uid] = True
                self.buff_len[uid]  = u.buffer.shape[-1]
                self.init_ts_idx[uid] = self.ts_buff_size - self.buff_len[uid]
                self.first_idx[uid] = n_u_vars
                n_u_vars += u.dim
            else: # initialize init_ts_idx and first_idx for source units
                # To get past values of source units we no longer call their get_act
                # function, but instead we rely on the values stored in acts
                self.init_ts_idx[uid] = self.ts_buff_size - (int(round(u.delay/self.min_delay))
                                                          * self.min_buff_size)
                self.first_idx[uid] = n_u_vars
                n_u_vars += 1
        # get a version of self.delays where time units are number of buffer intervals
        self.step_dels = [ [ self.min_buff_size*int(round(d/self.min_delay)) for d in l]
                                                                   for l in self.delays]
        # If there are plants, you'll need to store past values for all their state 
        # variables. Since the indexes of units and plants are independent, you need to
        # create an index for each state variable, in a way that won't collide with the
        # unit indexes. Plants are treated a bit differently from units due to the fact
        # that units only transmit their activity (first state variable).
        # p_st_var_idx[i][j] provides the index of the j-th state variable from the i-th 
        # plant in the acts array, and in the inp_src list (defined below).
        # n_plant_vars counts the total number of state variables from all plants,
        # just as n_u_vars counted this for units.
        n_plant_vars = 0
        if self.n_plants > 0:   # if there are plants
            index = n_u_vars # initial index (row in acts) for the plant
            self.p_st_var_idx = [[] for _ in range(self.n_plants)] 
            for pid, plant in enumerate(self.plants):
                n_plant_vars += plant.dim
                for var in range(plant.dim):
                    self.p_st_var_idx[pid].append(index)
                    index += 1
        # For each unit obtain a vector with the index of input units or plants in acts
        self.inp_src = [ [self.first_idx[syn.preID] for syn in l] for l in self.syns ]
        # TODO: this may cause an index error when there are more plants than units
        # The initialization of inp_src above will fail if there are plants. Modifying it.
        if self.n_plants > 0:
            for idx1, l in enumerate(self.syns):
                for idx2, syn in enumerate(l):
                    if hasattr(syn, 'plant_out'): # synapse comes from a plant
                        self.inp_src[idx1][idx2] = self.p_st_var_idx[syn.plant_id][syn.plant_out]
        #======================================================================
        # Creating the acts array
        self.acts = np.zeros((n_u_vars+n_plant_vars, len(self.ts)), dtype=self.bf_type)
        #======================================================================
        # acts_idx[u] is a complex index that allows unit u to extract from acts all
        # the inputs it receives at each substep of the current timestep.
        self.acts_idx = [[] for _ in range(self.n_units)]
        for uid, u in enumerate(self.units):
            fix = self.first_idx[uid]
            if hasattr(u, 'buffer'):
                # initializing acts and acts_idx
                self.acts[fix:fix+u.dim, self.init_ts_idx[uid]:] = \
                          np.array([u.init_val] * self.buff_len[uid]).transpose() 
                idx1 = [ [src]*self.min_buff_size for src in self.inp_src[uid] ]
                idx2 = [list(range(self.ts_buff_size - self.step_dels[uid][inp] - 1, 
                        self.ts_buff_size - self.step_dels[uid][inp] - 1 + self.min_buff_size))
                        for inp in range(len(self.inp_src[uid]))]
                self.acts_idx[uid] = (idx1, idx2)
            else:  # for source units, also initialize their rows in acts
                row = np.array([u.get_act(t) for t in self.ts[:]])
                # sometimes the source units have not been initialized, 
                # and return 'None' types. Thus this check:
                if not any([v is None for v in row]):
                    if not (np.isnan(row)).any():
                        self.acts[fix,:] = row 
        # Reinitializing the unit buffers as views of act, and times as views of ts
        self.link_unit_buffers()
        # specify the integration function for all units
        for uid, u in enumerate(self.units):
            if self.has_buffer[uid]:
                if u.integ_meth in ["odeint", "solve_ivp", "euler"]:
                    if u.multidim:
                        u.flat_update = u.flat_euler_update_md
                    else:
                        u.flat_update = u.flat_euler_update
                    
                    if u.integ_meth in ["odeint", "solve_ivp"]:
                        pass
                        #from warnings import warn
                        #warn('Integration method ' + u.integ_meth + \
                        #     ' substituted by Forward Euler in some units', UserWarning)
                    
                elif u.integ_meth == "euler_maru":
                    if u.multidim:
                        u.flat_update = u.flat_euler_maru_update_md
                        if not hasattr(u, 'mudt_vec') or not hasattr(u, 'sqrdt'):
                            raise AssertionError('A unit without both the mudt_vec and '+
                              'sqrdt attributes requested the flat euler maru ' +
                              'integration method.')
                    else:
                        if not hasattr(u, 'mudt') or not hasattr(u, 'sqrdt'):
                            raise AssertionError('A unit without both the mudt and sqrdt '+
                              'attributes requested the flat euler_maru integration method.')
                        u.flat_update = u.flat_euler_maru_update
                elif u.integ_meth == "exp_euler" and not u.multidim:
                    if not hasattr(u, 'mudt') or not hasattr(u, 'sqrdt'):
                        raise AssertionError('A unit without both the mudt and sqrdt '+
                            'attributes requested the flat euler_maru integration method.')
                    if not (hasattr(u, 'dt_fun_eu') and callable(u.dt_fun_eu)):
                        raise AssertionError('flat exponential Euler integration requires '
                                           + 'a "dt_fun_eu" derivatives function.')
                    u.flat_update = u.flat_exp_euler_update
                else:
                    raise NotImplementedError('The specified integration method is not \
                                               implemented for flat networks')
        # Reinitializing the buffers of plants as views of acts, times as views of ts
        for plant in self.plants:
            svi = self.p_st_var_idx[plant.ID][0]
            plant.buffer = np.ndarray(shape=(plant.dim, self.ts.size),
                           buffer=self.acts[svi:svi+plant.dim, :],
                           dtype=self.bf_type) 
            plant.times = self.ts.view()
            plant.buff_width = self.ts.size
            plant.offset = plant.buff_width - self.min_buff_size
            # initialize buffer
            init_buff = np.transpose(np.array([plant.init_state]*plant.buff_width))
            np.copyto(plant.buffer, init_buff)
        # At one point I needed to track the value of input sums with a source unit.
        # Initializing the inp_sum arrays permits initializing the function of those
        # source units before the simulation starts.
        for uid, u in enumerate(self.units):
            if u.multiport:
                if hasattr(u, 'needs_mp_inp_sum') and u.needs_mp_inp_sum:
                    u.upd_flat_mp_inp_sum(0.)
                else:
                    u.upd_flat_inp_sum(0.)
        #*****************
        self.flat = True 
        #*****************
        # At this point the functions in self.act for plants are for the non-flat
        # buffers. Thus we replace them with new ones, but this time self.flat = True
        if self.n_plants > 0:
            for idx_l, l in enumerate(self.syns):
                for idx_s, syn in enumerate(l):
                    if hasattr(syn, 'plant_out'): # synapse comes from a plant
                        self.act[idx_l][idx_s] = \
                            self.plants[syn.plant_id].get_state_var_fun(syn.plant_out)
        # experimental bit to try parallelizing
        #self.u_upd_funcs = []
        #for uid, u in enumerate(self.units):
        #    if self.has_buffer[uid]:
        #        if u.multiport and u.needs_mp_inp_sum:
        #            self.u_upd_funcs.append(u.upd_flat_mp_inp_sum)
        #        else:
        #            self.u_upd_funcs.append(u.upd_flat_inp_sum)
        #    else:
        #        self.u_upd_funcs.append(lambda t: None)
        #self.pool = ProcessingPool(nodes=10)


    def link_unit_buffers(self):
        """ Initializes the buffer, times, acts, and step_inps of all units.
        
            The important thing about this initialization is that buffer,
            times, and acts are initialized as views of the acts and times
            arrays of this network object.
            
            This routine is outside the body of network.flatten because it is
            useful on its own sometimes. In particular, when the network is
            copied, sometimes the link between unit.acts and network.acts is
            lost.
        """
        for uid, u in enumerate(self.units):
            if self.has_buffer[uid]:
                fix = self.first_idx[uid]
                if u.multidim:
                    """
                    u.buffer = np.ndarray(shape=u.buffer.shape,
                               buffer=self.acts[fix:fix+u.dim,self.init_ts_idx[uid]:],
                               dtype=self.bf_type)
                    """
                    u.buffer = self.acts[fix:fix+u.dim, self.init_ts_idx[uid]:]
                else:
                    """
                    u.buffer = np.ndarray(shape=(self.buff_len[uid]), 
                                          buffer=self.acts[fix,self.init_ts_idx[uid]:],
                                          dtype=self.bf_type)
                    """
                    u.buffer = self.acts[fix, self.init_ts_idx[uid]:]
                    
                #u.times = np.ndarray(shape=(self.buff_len[uid]), 
                #                     buffer=self.ts[self.init_ts_idx[uid]:], dtype=self.bf_type)
                u.times = self.ts[self.init_ts_idx[uid]:]
                """
                # Below is code used to create a version where the step_inps matrix is
                # obtained using logical indexing. Somehow it is slower...
                u.acts = np.frombuffer(self.acts.data)
                # Using frombuffer creates a 'flat' view, which the unit will address
                # using a 1-D 'acts_idx' array that we'll create next
                u.acts_idx = [False] * self.acts.size
                idx = self.acts_idx[uid]
                for i,l in enumerate(idx[0]):
                    for j,e in enumerate(l):
                        u.acts_idx[e*self.ts_buff_size + idx[1][i][j]] = True
                u.n_inps = len(idx[0])
                """
                u.acts = self.acts.view()
                u.acts_idx = self.acts_idx[uid]
                u.act_buff = self.acts[fix, self.init_ts_idx[uid]:]
                # step_inps is a 2D numpy array. step_inps[j,k] provides the activity
                # of the j-th input to unit i in the k-th substep of the current timestep.
                u.step_inps = self.acts[self.acts_idx[uid]]
                """
                # experimental bit to test with numba
                #-----------------------------------------------------
                u.flat_acts_idx = np.array([False] * self.acts.size)
                idx = self.acts_idx[uid]
                for i,l in enumerate(idx[0]):
                    for j,e in enumerate(l):
                        u.flat_acts_idx[e*self.ts_buff_size + idx[1][i][j]] = True
                u.n_inps = len(idx[0])
                #-----------------------------------------------------
                """

    def get_act(self, uid, t):
        """ Get the activity of unit with ID 'uid' at time 't'.

            t should be within the range of values in the unit's buffer.
            This method is only valid for flat networks.
        """
        return cython_get_act3(t, self.ts[self.init_ts_idx[uid]], 
                               self.ts_bit, self.ts_buff_size - self.init_ts_idx[uid], 
                               self.acts[self.first_idx[uid]][self.init_ts_idx[uid]:])


    def get_act_by_step(self, uid, s):
        """ Get the activity of unit with ID 'uid' as it was 's' buffer time steps before.

            's' should not be larger than the number of steps in the unit's buffer.
            A buffer time steps corresponds to the time interval between consecutive
            buffer entries, namely min_delay/min_buff_size.

            This method is to be used with the second type of flat networks.
        """
        return self.acts[self.first_idx[uid]][-1 - s]


    def flat_update(self, time):
        """ Updates all state variables by advancing them one min_delay time step. """
        # update the times array
        self.ts += self.min_delay 
        #self.ts = np.roll(self.ts, -self.min_buff_size)
        #self.ts[self.ts_buff_size-self.min_buff_size:] = self.ts_grid[1:]+time
        #----------------------------------------------------------------------
        # update input sums
        for uid, u in enumerate(self.units):
            if self.has_buffer[uid]:
                if u.multiport and u.needs_mp_inp_sum:
                    u.upd_flat_mp_inp_sum(time)
                else:
                    u.upd_flat_inp_sum(time)
        """
        # parallel update of input sums
        self.units = self.pool.map(lambda u: upd_unit(u, time), self.units)
        self.pool.close()
        self.pool.join()
        #with Pool(10) as p:
        #    list(p.map(lambda f: f(time), self.u_upd_funcs))
        #    p.close()
        """
        #----------------------------------------------------------------------
        # roll the full acts array
        base = self.ts.size - self.min_buff_size
        self.acts[:,:base] = self.acts[:,self.min_buff_size:]
        # update buffers
        for uid, u in enumerate(self.units):
            if self.has_buffer[uid]:
                u.flat_update(time)
        for p in self.plants:
            p.flat_update(time)
        # update activities of source units and handle requirements
        for uid, u in enumerate(self.units):
            if not self.has_buffer[uid]:
                self.acts[self.first_idx[uid],base:] = [u.get_act(t) for t in self.ts[base:]]
            # handle requirements
            u.pre_syn_update(time)
            u.last_time = time # important to have it after pre_syn_update
        # update synapses
        for synli in self.syns:
            for syn in synli:
                syn.update(time)


    def flat_run(self, total_time):
        """ Simulate a flattened network for the given time. 
        
            Flat networks keep a single numpy array in the netwok object with 
            the contents of all buffers. However, all units have buffers which are views of
            a slice of that array. 
        """
        if not self.flat:
            self.flatten()
        Nsteps = int(total_time/self.min_delay)  # total number of simulation steps
        unit_store = [np.zeros(Nsteps) for i in range(self.n_units)] # arrays to store unit activities
        plant_store = [np.zeros((Nsteps,p.dim)) for p in self.plants] # arrays to store plant steps
        times = np.zeros(Nsteps) + self.sim_time # array to store initial time of simulation steps

        for step in range(Nsteps):
            times[step] = self.sim_time # self.sim_time persists between calls to network.run()
            
            # store current unit activities
            for uid, unit in enumerate(self.units):
                unit_store[uid][step] = self.get_act(uid, self.sim_time)
                #unit_store[uid][step] = self.get_act_by_step(uid, 0)
           
            # store current plant state variables 
            for pid, plant in enumerate(self.plants):
                plant_store[pid][step,:] = plant.get_state(self.sim_time)
            
            # update units and plants
            self.flat_update(self.sim_time)
            self.sim_time += self.min_delay

        return times, unit_store, plant_store


    def save_state(self):
        """ Create a dictionary with the network's state.

            This state dictionary can be used to set the network's state using
            the network.set_state method. After the state is set, subsequent 
            simulations should be the same as when the state was saved.

            The state dictionary does not save the whole network configuration,
            but only the information necessary to continue simulations (e.g.
            buffer contents, synaptic weights), and some information regarding
            the network structure (for sanity tests in the set_state method). 
            
            It is assumed that save_state and set_state will be used in
            almost identical networks. The only differences contemplated are in
            how much each network has been simulated, the functions of the
            source units, and the state of the plants.

            Returns:
                state: A dictionary with the information necessary to update the
                       simulation. The dictionary contains these entries:
                    units: list with the type of each unit.
                    syns: for each synapse: (source, type, weight).
                    plants: list with the type of each plant.
                    pl_syns: for each plant input and port: (source, weight).
                    delays: a copy of network.delays
                    flat: a copy of network.flat (True if network is flat).
                    unit_buff_t: buffers and times for units if net not flat.
                    plant_buff_t: buffers and times for plants if net not flat.
                    acts: copy of network.acts if network flat.
                    ts: copy of network.ts if network flat.
                    lpf: buffers used for low-pass filtered activity.
                    sim_time: a copy of network.sim_time
        """
        state = {}
        state['units'] = [u.type for u in self.units]
        state['syns'] = [[] for sl in self.syns]
        for syn_idx in range(len(self.units)):
            for syn in self.syns[syn_idx]:
                state['syns'][syn_idx].append(
                                     (syn.preID,syn.type,syn.w))
        state['plants'] = [p.type for p in self.plants]
        state['pl_syns'] = [[] for p in self.plants]
        for pl_idx in range(len(self.plants)):
            for port in range(self.plants[pl_idx].inp_dim):
                state['pl_syns'][pl_idx].append([(syn.preID,syn.w) for 
                                 syn in self.plants[pl_idx].inp_syns[port]])
        state['delays'] = self.delays
        state['flat'] = self.flat
        # all buffers should be copies, otherwise they'll continue to update
        if not self.flat:
            state['unit_buff_t'] = [() for _ in self.units]
            state['plant_buff_t'] = [() for _ in self.plants]
            for uid, u in enumerate(self.units):
                if hasattr(u, 'buffer'):
                    state['unit_buff_t'][uid] = (u.buffer.copy(),u.times.copy())
            for pid, p in enumerate(self.plants):
                state['plant_buff_t'][pid] = (p.buffer.copy(), p.times.copy())
        else:
            state['acts'] = self.acts.copy()
            state['ts'] = self.ts.copy()
        state['lpf'] = [{} for _ in self.units]
        for uid, u in enumerate(self.units):
            if hasattr(u, 'lpf_fast_buff'):
                state['lpf'][uid]['lpf_fast_buff'] = u.lpf_fast_buff.copy()
            if hasattr(u, 'lpf_mid_buff'):
                state['lpf'][uid]['lpf_mid_buff'] = u.lpf_mid_buff.copy()
            if hasattr(u, 'lpf_slow_buff'):
                state['lpf'][uid]['lpf_slow_buff'] = u.lpf_slow_buff.copy()
            if hasattr(u, 'lpf_mid_inp_sum'):
                state['lpf'][uid]['lpf_mid_inp_sum'] = u.lpf_mid_inp_sum.copy()
        state['sim_time'] = self.sim_time

        return state
                

    def set_state(self, state):
        """ Set the network to a previously saved state.

            This method receives a state dictionary created with the
            save_state() function, and transfers its values into the network.

            Args:
                state: A dictionary. See network.save_state.
                    units: list with the type of each unit.
                    syns: for each synapse: (source, type, weight).
                    plants: list with the type of each plant.
                    pl_syns: for each plant input and port: (source, weight).
                    delays: a copy of network.delays
                    flat: a copy of network.flat (True if network is flat).
                    unit_buff_t: buffers and times for units if net not flat.
                    plant_buff_t: buffers and times for plants if net not flat.
                    acts: copy of network.acts if network flat.
                    ts: copy of network.ts if network flat.
                    lpf: buffers used for low-pass filtered activity.
                    sim_time: a copy of network.sim_time
        """
        #TODO: Seems like I forgot to restor the lpf buffers. Also, all
        # requirement variables.

        # testing network has the same signature
        for uid, u in enumerate(self.units):
            if u.type != state['units'][uid]:
                raise ValueError('Corresponding units are not of the same ' +
                        'type in received state and in the network')
        for uid, syn_list in enumerate(self.syns):
            for syn_id, syn in enumerate(syn_list):
                if syn.preID != state['syns'][uid][syn_id][0]:
                    raise ValueError('Connectivity structure differs ' +
                            'in the received state and in the network')
                if syn.type != state['syns'][uid][syn_id][1]:
                    raise ValueError('Synapse types differ in the ' +
                            'received state and in the network')
        for pl_id, plant in enumerate(self.plants):
            for po_id, syn_list in enumerate(plant.inp_syns):
                for syn_id, syn in enumerate(syn_list):
                    if syn.preID != state['pl_syns'][pl_id][po_id][syn_id][0]:
                        raise ValueError('Connections to plants differ ' +
                                'in received state and in the network')
        if self.flat != state['flat']:
            raise ValueError('Received state does not agree on flattening')
        if self.delays != state['delays']:
            raise ValueError('Delays do not agree in received state and ' +
                             'in the network')
        # restoring the state
        if not self.flat: # when network not flat
            # copying buffers and times into the units
            for uid, u in enumerate(self.units):
                if hasattr(u, 'buffer'):
                    u.buffer = state['unit_buff_t'][uid][0]
                    u.times = state['unit_buff_t'][uid][1]
                    if u.multidim:
                        u.act_buff = u.buffer[0,:]
                    else:
                        u.act_buff = u.buffer.view()
                    if u.using_interp1d:
                        u.upd_interpolator()
            # copying buffers and times into the plants
            for pid, p in enumerate(self.plants):
                p.buffer = state['plant_buff_t'][pid][0]
                p.times = state['plant_buff_t'][pid][1]
            # copying synaptic weights
            for uid, syn_list in enumerate(self.syns):
                for sid, syn in enumerate(syn_list):
                    syn.w = state['syns'][uid][sid][2]
        else: # flat network
            self.acts = state['acts']
            self.ts = state['ts']
            # link buffers as in self.flatten()
            self.link_unit_buffers()
            for plant in self.plants:
                svi = self.p_st_var_idx[plant.ID][0]
                plant.buffer = np.ndarray(shape=(plant.dim, self.ts.size),
                            buffer=self.acts[svi:svi+plant.dim, :],
                            dtype=self.bf_type) 
                plant.times = self.ts.view()
                plant.buff_width = self.ts.size
                plant.offset = plant.buff_width - self.min_buff_size
           
        ## linking plants...
        ## TODO: Might need to update plant.inputs, plant.inp_syns as in append_inputs
        if self.n_plants > 0:
            for idx_l, l in enumerate(self.syns):
                for idx_s, syn in enumerate(l):
                    if hasattr(syn, 'plant_out'): # synapse comes from a plant
                        self.act[idx_l][idx_s] = \
                            self.plants[syn.plant_id].get_state_var_fun(syn.plant_out)

        self.sim_time = state['sim_time']


    def run(self, total_time):
        """
        Simulate the network for the given time.

        This method takes steps of 'min_delay' length, in which the units, synapses 
        and plants use their own methods to advance their state variables.

        After run(T) is finished, calling run(T) again continues the simulation
        starting at the last state of the previous simulation.

        Args:
            total_time: time that the simulation will last.
        
        Returns:
            The method returns a 3-tuple (times, unit_store, plant_store): 
            times: a numpy array with  the simulation times when the update functions 
                      were called. These times will begin at the initial simulation time, 
                      and advance in 'min_delay' increments until 'total_time' is completed.
            unit_store: a list of numpy arrays. unit_store[i][j] contains the activity
                        of the i-th unit at time j-th timepoint (e.g. at times[j]).
            plant_store: a list of 2-dimensional numpy arrays. plant_store[i][j,k] is the value
                         of the k-th state variable, at the j-th timepoint, for the i-th plant.

        Raises:
            AssertionError
        """
        if self.flat:
            raise AssertionError('The run method is not used with flattened networks')
        Nsteps = int(total_time/self.min_delay) # total number of simulation steps
        unit_store = [np.zeros(Nsteps) for i in range(self.n_units)] # arrays to
                                                          #store unit activities
        plant_store = [np.zeros((Nsteps,p.dim)) for p in self.plants] # arrays to
                                                             # store plant states
        times = np.zeros(Nsteps) + self.sim_time # array to store initial time of
                                                 # simulation steps

        for step in range(Nsteps):
            times[step] = self.sim_time # sim_time persists between calls to network.run()
            
            # store current unit activities
            for uid, unit in enumerate(self.units):
                unit_store[uid][step] = unit.get_act(self.sim_time)
           
            # store current plant state variables 
            for pid, plant in enumerate(self.plants):
                plant_store[pid][step,:] = plant.get_state(self.sim_time)
            
            # update units
            for unit in self.units:
                unit.update(self.sim_time)

            # update plants
            for plant in self.plants:
                plant.update(self.sim_time)

            self.sim_time += self.min_delay

        return times, unit_store, plant_store

