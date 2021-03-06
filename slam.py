"""
GraphSlam 3D Implementation.

TODO : support global initialization (initial pose + landmark estimates)
"""
import numpy as np
from utils import qmath_np

def block(ar):
    """ Convert Block Matrix to Dense Matrix """
    ni,nj,nk,nl = ar.shape
    return np.swapaxes(ar, 1, 2).reshape(ni*nk, nj*nl)

def unblock(ar, nknl):
    """ Convert Dense Matrix to Block Matrix """
    nk,nl = nknl
    nink, njnl = ar.shape
    ni = nink/nk
    nj = njnl/nl
    return ar.reshape(ni,nk,nj,nl).swapaxes(1,2)

class GraphSlam3(object):
    def __init__(self, n_l, l=0.0):
        self._nodes = {}
        self._n_l = n_l
        self._lambda = l

    def add_edge(self, x, i0, i1):
        n = self._nodes
        p0, q0 = qmath_np.x2pq(n[i0])
        p1, q1 = qmath_np.x2pq(n[i1])
        dp, dq = qmath_np.x2pq(x)
        Aij, Bij, eij = qmath_np.Aij_Bij_eij(p0,p1,dp,q0,q1,dq)
        return Aij, Bij, eij

    def initialize(self, x0):
        n = 2 + self._n_l # [x0,x1,l0...ln]
        self._H = np.zeros((n,n,6,6), dtype=np.float64)
        self._b = np.zeros((n,1,6,1), dtype=np.float64)
        self._H[0,0] = np.eye(6)
        self._nodes[0] = x0
        # TODO : Are the below initializations necessary?
        #p, q = qmath_np.x2pq(x0)
        #x = np.concatenate([p,qmath_np.T(q)], axis=-1)
        #self._b[0,0,:,0] = x

    def initialize_n(self, x0s):
        """ Initialize all nodes with estimates """
        for (xi, x) in x0s:
            self._nodes[xi] = x
        # TODO : implement
        # TODO : separate out online / offline classes
        # with shared base that implements add_edge() / add-node() / initialize()
        # TODO : consider folding initialize_n into initialize by checking if x0 is iterable in [n,7]

    def step(self, x=None, ox=None, zs=None):
        """ Online Version """

        # " expand "
        self._H[1,:] = 0.0
        self._H[:,1] = 0.0
        self._b[1]   = 0.0

        zis = [] # updates list

        # apply motion updates first
        if x is not None:
            self._nodes[1] = qmath_np.xadd_rel(self._nodes[0], x, T=False)
            zis.append(1)
            #zs.append([0, 1, x, ox])
            # TODO : incorporate omega_x somehow
            # simply adding x0->x1 to zs did not work

        # H and b are organized as (X0, X1, L0, L1, ...)
        # where X0 is the previous position, and X1 is the current position.
        # Such that H[0,..] pertains to X0, and so on.

        # now with observations ...
        for (z0, z1, z, o) in zs:
            zis.append(z1)
            if z1 not in self._nodes:
                # initial guess
                self._nodes[z1] = qmath_np.xadd_rel(
                        self._nodes[z0], z, T=False)
                # no need to compute deltas for initial guesses
                # (will be zero) 
                continue
            Aij, Bij, eij = self.add_edge(z, z0, z1)
            self._H[z0,z0] += Aij.T.dot(o).dot(Aij)
            self._H[z0,z1] += Aij.T.dot(o).dot(Bij)
            self._H[z1,z0] += Bij.T.dot(o).dot(Aij)
            self._H[z1,z1] += Bij.T.dot(o).dot(Bij)
            self._b[z0]   += Aij.T.dot(o).dot(eij)
            self._b[z1]   += Bij.T.dot(o).dot(eij)

        H00 = block(self._H[:1,:1])
        H01 = block(self._H[:1,1:])
        H10 = block(self._H[1:,:1])
        H11 = block(self._H[1:,1:])

        B00 = block(self._b[:1,:1])
        B10 = block(self._b[1:,:1])

        AtBi = np.matmul(H10, np.linalg.pinv(H00))
        XiP  = B10

        # fold previous information into new matrix

        H = H11 - np.matmul(AtBi, H01)
        B = B10 - np.matmul(AtBi, B00)

        mI = self._lambda * np.eye(*H.shape) # marquardt damping
        #dx = np.matmul(np.linalg.pinv(H), -B)
        dx = np.linalg.lstsq(H+mI,-B, rcond=None)[0]
        dx = np.reshape(dx, [-1,6]) # [x1, l0, ... ln]

        for i in zis:
            self._nodes[i] = qmath_np.xadd_abs(self._nodes[i], dx[i-1])

        #for i in range(1, 2+self._n_l):
        #    if i in self._nodes:
        #        self._nodes[i] = qmath_np.xadd(self._nodes[i], dx[i-1])

        ##dx2 = np.matmul(np.linalg.pinv(block(self._H)), -block(self._b))
        #dx2 = np.linalg.lstsq(block(self._H), -block(self._b), rcond=None)[0]
        #dx2 = np.reshape(dx2, [-1,6])

        ##print 'dx2', dx2[1:]
        #for i in range(0, 2+self._n_l):
        #    self._nodes[i] = qmath_np.xadd(self._nodes[i], dx2[i])

        # replace previous node with current position
        self._nodes[0] = self._nodes[1].copy()

        H = unblock(H, (6,6))
        B = unblock(B, (6,1))

        # assign at appropriate places, with x_0 being updated with x_1
        self._H[:1,:1] = H[:1,:1]
        self._H[:1,2:] = H[:1,1:]
        self._H[2:,:1] = H[1:,:1]
        self._H[2:,2:] = H[1:,1:]
        self._b[:1] = B[:1]
        self._b[2:] = B[1:]

        x = [self._nodes[k] for k in sorted(self._nodes.keys())]
        return x

    def run(self, zs, max_nodes, n_iter=10, tol=1e-4, debug=False):
        """ Offline version """

        n = max_nodes

        for it in range(n_iter): # iterate 10 times for convergence
            H = np.zeros((n,n,6,6), dtype=np.float64)
            b = np.zeros((n,1,6,1), dtype=np.float64)

            for (z0, z1, z, o) in zs:
                if z1 not in self._nodes:
                    # add initial guess to node
                    self._nodes[z1] = qmath_np.xadd_rel(self._nodes[z0], z, T=False)

                Aij, Bij, eij = self.add_edge(z, z0, z1)
                H[z0,z0] += Aij.T.dot(o).dot(Aij)
                H[z0,z1] += Aij.T.dot(o).dot(Bij)
                H[z1,z0] += Bij.T.dot(o).dot(Aij)
                H[z1,z1] += Bij.T.dot(o).dot(Bij)
                b[z0]    += Aij.T.dot(o).dot(eij)
                b[z1]    += Bij.T.dot(o).dot(eij)


            H[0,0] += np.eye(6)
            H = block(H)
            b = block(b)

            # solve ...

            # marquardt - somehow makes it worse or something
            #mI = self._lambda * np.eye(*H.shape)
            #dx = np.linalg.lstsq(H+mI,-b, rcond=None)[0]

            dx = np.linalg.lstsq(H,-b, rcond=None)[0]
            dx = np.reshape(dx, [-1,6])

            # update
            for i in range(max_nodes):
                if i in self._nodes:
                    self._nodes[i] = qmath_np.xadd_abs(self._nodes[i], dx[i])

            # check convergence
            delta = np.mean(np.square(dx))
            if debug:
                print('delta', delta)
            if delta < tol:
                break

        x = [self._nodes[k] for k in sorted(self._nodes.keys())]
        return x
