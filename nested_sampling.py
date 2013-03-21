"""classes and routines for running nested sampling"""
import random
import numpy as np
import sys
import multiprocessing as mp
#this import fixes some bugs in how multiprocessing deals with exceptions
import pygmin.utils.fix_multiprocessing

class MonteCarloChain(object):
    """Class for doing a Monte Carlo chain
    

    Parameters
    -----------
    potential : pygmin Potentials
        attribute of system with member function getEnergy (in essence a
        particular potential energy function)
    x : array
        are the coordinates
    takestep : callable takestep object
        take a random montecarlo step, imported from pygmin: takestep(x) makes
        a move from x
    Emax : float
        energy upperbound
    energy : pygmin LJCluster
        energy of a given configuration
    accept_test : list of callables
        it's an array of pointers to functions. The dereferenced functions
        operate a set of tests on the energy/configuration.
    events : list fo callables
        it's an array of pointers to functions. This is general and not
        compulsury e.g. can use if you want to do something with the new
        configuration for the guy.

    Attributes
    ----------
    nsteps : integer
        tot. number of steps
    naccepts : integer
        tot. number of accepted configurations
    xnew : array
        new proposed configuration
    accept : boolean
        true or false if energy constraint is satisfied

    Notes
    -----
    defines parameters for a monte carlo chain from an initial configuration x
    with niter iterations. The step will be accepted subject only to a maximum
    energy criterion and geometric constraints on the configuration.  

    See Also
    --------
    NestedSampling
    """
    def __init__(self, potential, takestep, accept_tests=None, events=None):
        self.potential = potential
        self.takestep = takestep
        self.accept_tests = accept_tests
        self.events = events
    
    def __call__(self, x0, mciter, stepsize, Emax):
        return self.run(x0, mciter, stepsize, Emax)
    
    def run(self, x0, mciter, stepsize, Emax):        
        self.x = x0
        self.Emax = Emax
        self.energy = self.potential.getEnergy(self.x)
        self.mciter = mciter
        self.takestep.size = stepsize
        
        self.nsteps = 0
        self.naccept = 0
        
        if not self.test_configuration(self.x, 0.):
            print "ERROR: initial configuration for monte carlo chain failed configuration test"
            from pygmin.utils.xyz import write_xyz
            with open("error.xyz", "w") as fout:
                write_xyz(fout, self.x)
        
        for i in xrange(self.mciter):
            self.step()
        
        return self


        
    def test_configuration(self, x, e):
        if self.accept_tests is not None:
            for test in self.accept_tests:
                if not test(energy=e, coords=x):
                    return False
        return True

    def step(self):
        """
        Do one iteration in the Monte Carlo chain

        Notes
        -----
        copy current configuration x to xnew and then perform a trial move on
        xnew. Accept if the configuration of the new energy is less than Emax
        and if accept_tests are satisfied. If true update x to xnew. Finally,
        optionally applies all events functions to x.  
        """
        xnew = self.x.copy()
        
        # displace configuration
        self.takestep(xnew)
        
        # get energy
        e = self.potential.getEnergy(xnew)
        
        accept = e < self.Emax

        if accept:
            accept = self.test_configuration(xnew, e) 

        if accept:
            self.x = xnew
            self.energy = e
            self.naccept += 1
        
        if self.events is not None:
            for event in self.events:
                event(coords=self.x, x=self.x, energy=self.energy, accept=accept)

        self.nsteps += 1

class Replica(object):
    """ Replica is simply a pair of types coordinates (x) and energy"""
    def __init__(self, x, energy):
        self.x = x.copy()
        self.energy = float(energy)

def mc_runner_wrapper(x_tuple):
    mc_runner = x_tuple[0]
    x0, mciter, stepsize, Emax = x_tuple[1:]
    return mc_runner(x0, mciter, stepsize, Emax) 

class NestedSampling(object):
    """the main class for implementing nested sampling

    Parameters
    ----------
    system : pygmin System
        is the particular system of interest, say LJCluster
    takestep : callable takestep object
        take a random montecarlo step, imported from pygmin: takestep(x) makes
        a move from x
    mciter : integer
        number of steps in markov chain (sampling)
    accept_test : list of callables
        it's an array of pointers to functions. The dereferenced functions
        operate a set of tests on the energy/configuration.
    mc_runner : callable
        use this object to do the Monte Carlo chain rather than the default
    
    Attributes
    ----------
    max_energies : list
        array of stored energies (at each step the highest energy configuration
        is stored and replaced by a valid configuration)
    replicas : list
        list of objects of type Replica
        """
    def __init__(self, system, nreplicas, takestep, mciter=100, accept_tests=None, mc_runner=None, nproc=1):
        self.system = system
        self.takestep = takestep
        self.mciter=mciter
        self.accept_tests = accept_tests
        self.nproc = nproc
        
        #choose between compiled and raw version of the mc_runner
        if mc_runner is None:
            self.mc_runner = MonteCarloChain(self.system.get_potential(), self.takestep, accept_tests=self.accept_tests)
        else:
            self.mc_runner = mc_runner
        
        self.max_energies = []
        
        self.setup_replicas(nreplicas)
    
        self.iter_number = 0
        
        print "nreplicas", len(self.replicas)
        print "mciter", self.mciter
        sys.stdout.flush()
        
    def do_monte_carlo_chain(self, configs, Emax, **kwargs):
        """
        from an initial configuration do a monte carlo chain with niter iterations, 
        
        the step will be accepted subject only to a maximum energy criterion.
        Re-iterates for mciter steps.  After each step update stepize to meet
        target_ratio. 
        """
        if self.nproc > 1:
            x_tuple = [(self.mc_runner, x, self.mciter, self.takestep.stepsize, Emax) for x, energy in configs]
            for t in x_tuple: #  debug check
                assert isinstance(t[1], np.ndarray), "%s" % str(t[1])

            pool = mp.Pool(processes=self.nproc)
            try:
                mclist = pool.map(mc_runner_wrapper, x_tuple)
                pool.close()
                pool.join()
            except:
                pool.terminate()
                pool.join()
                raise

        else:
            x0, energy = configs[0]
            mc = self.mc_runner(x0, self.mciter, self.takestep.stepsize, Emax)
            mclist = [mc]
        
            verbose = True
            if verbose:
                # print some data
                dist = np.linalg.norm(mc.x - x0)
                print "step:", self.iter_number, "%accept", float(mc.naccept) / mc.nsteps, \
                    "Enew", mc.energy, "Eold", energy, "Emax", Emax, "Emin", self.replicas[0].energy, \
                    "stepsize", self.takestep.stepsize, "distance", dist
            
            if mc.naccept == 0:
                sys.stderr.write("WARNING: zero steps accepted in the Monte Carlo chain %d\n")
                print >> sys.stderr, "WARNING: step:", self.iter_number, "%accept", float(mc.naccept) / mc.nsteps, \
                    "Enew", mc.energy, "Eold", energy, "Emax", Emax, "Emin", self.replicas[0].energy, \
                    "stepsize", self.takestep.stepsize, "distance", dist

        self.adjust_step_size(mclist)
        return mclist
    
    def create_replica(self):
        """
        creates a random configuration, evaluates its energy and creates the corresponding Replica object
        """
        x = self.system.get_random_configuration()
        pot = self.system.get_potential()
        e = pot.getEnergy(x)
        return Replica(x, e)
    
    def sort_replicas(self):
        """
        sorts the replicas in decreasing order of energy
        """
        self.replicas.sort(key=lambda r:r.energy)
    
    def setup_replicas(self, nreplicas):
        """
        creates nreplicas replicas and sorts them in decreasing order of energy
        """
        self.replicas = [self.create_replica() for i in range(nreplicas)]
        self.sort_replicas()
        print "min replica energy", self.replicas[0].energy
        print "max replica energy", self.replicas[-1].energy
    
    def adjust_step_size(self, mc):
        """
        Adjust the stepsize to keep the acceptance ratio at a good value

        Notes
        -----
        when ratio naccept/nsteps < target_ratio then decrease step size by a
        correction factor f (new-step-size = old-step-size*f, where 0<f<1),
        else if naccept/nsteps > target_ratio, then increases the step size
        (new-step-size = old-step-size/f, where 0<f<1), although this is
        increased with an upper bound: max_stepsize.
        """
        f = 0.8
        target_ratio = 0.7
        max_stepsize = 0.5
        ratio = float(sum(m.naccept for m in mc))/ sum(m.nsteps for m in mc)  
        
        if ratio < target_ratio:
            # reduce step size
            self.takestep.stepsize *= f
        else:
            self.takestep.stepsize /= f
        if self.takestep.stepsize > max_stepsize:
            self.takestep.stepsize = max_stepsize
        
    def pop_replica(self):
        """
        remove the replica with the largest energy (last item in the list) and store it in the max_energies array. 
        
        This is then used for integration.
        """
        # pull out the replica with the largest energy
        rmax = self.replicas.pop()
        print "Epop:", rmax.energy, "n_replicas", len(self.replicas)
        
        #remove other nproc-1 replica
        length = self.nproc-1
        for i in xrange(length):
            self.replicas.pop()
        
        # add store it for later analysis
        self.max_energies.append(rmax.energy)
        return rmax

    def get_starting_configuration_from_replicas(self):
        # choose a replica randomly
        if self.nproc > 1:
            rlist = random.sample(self.replicas, self.nproc)
            configlist = [(r.x, r.energy) for r in rlist]
            return configlist
        else:
            rstart = random.choice(self.replicas)
            return [(rstart.x, rstart.energy)]

    def get_starting_configuration(self, Emax):
        return self.get_starting_configuration_from_replicas()
    
    def add_new_replica(self, x, energy):
        rnew = Replica(x, energy)
        self.replicas.append(rnew)
        return rnew
    
    def one_iteration(self):
        rmax = self.pop_replica()
        Emax = rmax.energy
        configs = self.get_starting_configuration(Emax)
        # note configs is a list of starting configurations.
        # but a list of length 1 if self.nproc == 1
        
        mclist = self.do_monte_carlo_chain(configs, Emax)

        for mc in mclist:
            rnew = self.add_new_replica(mc.x, mc.energy)
            
        self.iter_number += 1        
        self.sort_replicas()



if __name__ == "__main__":
    from lj_run import LJClusterNew
    from pygmin.takestep import RandomDisplacement
    natoms = 13
    nreplicas = 1000
    mciter = 100
    system = LJClusterNew(natoms)
    
    
    ns = NestedSampling(system, nreplicas, RandomDisplacement(stepsize=0.5), mciter=mciter)
    for i in range(nreplicas * 300):
        ediff = ns.replicas[-1].energy - ns.replicas[0].energy
        if ediff < .01: break  
        ns.one_iteration()
    print ns.max_energies
    print "min replica energy", ns.replicas[0].energy
    print "max replica energy", ns.replicas[-1].energy
    
    with open("max_energies", "w") as fout:
        fout.write( "\n".join([ str(e) for e in ns.max_energies]) )
        fout.write("\n")
        
    
    if True:
        import pylab as pl
        e = np.array(ns.max_energies)
        pl.plot(e)
        pl.show()

    
