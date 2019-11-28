import os
import sys
import hashlib
import numpy as np
import torch

import torch_cpp
import deepsmlm.test.utils_ci as tutil


def at_least_one_dim(*args):
    for arg in args:
        if arg.dim() == 0:
            arg.unsqueeze_(0)


def same_shape_tensor(dim, *args):
    for i in range(args.__len__() - 1):
        if args[i].size(dim) == args[i + 1].size(dim):
            continue
        else:
            return False

    return True


def same_dim_tensor(*args):
    for i in range(args.__len__() - 1):
        if args[i].dim() == args[i + 1].dim():
            continue
        else:
            return False

    return True


class EmitterSet:
    """
    Class, storing a set of emitters. Each attribute is a torch.Tensor.
    """
    eq_precision = 1E-6

    def __init__(self, xyz, phot, frame_ix, id=None, prob=None, bg=None, xyz_cr=None, phot_cr=None, bg_cr=None,
                 sanity_check=True):
        """
        Constructor. Coordinates, photons, frame_ix must be provided. Id is optional.

        :param xyz: torch.Tensor of size N x 3 (2). x, y, z are in arbitrary units.
        often [x] in px, [y] in px, [z] in nm.
        :param phot: torch.Tensor of size N. number of photons.
        :param frame_ix: integer or torch.Tensor. If it's one element, the whole set belongs to
        the frame, if it's a tensor, it must be of length N.
        :param id: torch.Tensor of size N. id of an emitter. -1 is an arbitrary non uniquely used
         fallback id.
        :param prob: torch.Tensor of size N. probability of observation. will be 1 by default.
        :param bg: constant assumed background value, or N x {Background - Dim}
        :param xyz_cr: Cramer Rao Bound of xyz
        :param phot_cr: Cramer Rao of phot
        :param bg_cr: Cramer Rao of background
        """
        num_emitter_input = int(xyz.shape[0]) if xyz.shape[0] != 0 else 0

        if num_emitter_input != 0:
            self.xyz = xyz if xyz.shape[1] == 3 else torch.cat((xyz, torch.zeros_like(xyz[:, [0]])), 1)
            self.phot = phot.type(xyz.dtype)
            self.frame_ix = frame_ix.type(xyz.dtype)
            self.id = id if id is not None else -torch.ones_like(frame_ix).type(xyz.dtype)
            self.prob = prob if prob is not None else torch.ones_like(frame_ix).type(xyz.dtype)
            self.bg = bg if bg is not None else float('nan') * torch.ones_like(frame_ix).type(xyz.dtype)
            self.xyz_cr = xyz_cr if xyz_cr is not None else float('nan') * torch.ones_like(self.xyz).type(xyz.dtype)
            self.phot_cr = phot_cr if phot_cr is not None else float('nan') * torch.ones_like(self.phot).type(xyz.dtype)
            self.bg_cr = bg_cr if bg_cr is not None else float('nan') * torch.ones_like(self.bg).type(xyz.dtype)

        else:
            self.xyz = torch.zeros((0, 3), dtype=torch.float)
            self.phot = torch.zeros((0,), dtype=torch.float)
            self.frame_ix = torch.zeros((0,), dtype=torch.float)
            self.id = -torch.ones((0,), dtype=torch.float)
            self.prob = torch.ones((0,), dtype=torch.float)
            self.bg = float('nan') * torch.ones_like(self.prob)
            self.xyz_cr = float('nan') * torch.ones((0, 3), dtype=torch.float)
            self.phot_cr = float('nan') * torch.ones_like(self.prob)
            self.bg_cr = float('nan') * torch.ones_like(self.prob)

        self._sorted = False
        # get at least one_dim tensors
        at_least_one_dim(self.xyz,
                         self.phot,
                         self.frame_ix,
                         self.id,
                         self.prob,
                         self.bg,
                         self.xyz_cr,
                         self.phot_cr,
                         self.bg_cr)

        if sanity_check:
            self._sanity_check()

    @property
    def num_emitter(self):
        return int(self.xyz.shape[0]) if self.xyz.shape[0] != 0 else 0

    @property
    def xyz_scr(self):  # sqrt crlb
        return self.xyz_cr.sqrt()

    @property
    def phot_scr(self):
        return self.phot_cr.sqrt()

    @property
    def bg_scr(self):
        return self.bg_cr.sqrt()

    def _inplace_replace(self, em):
        """If self is derived class of EmitterSet, call the constructor of the parent instead of self.
        However, I don't know why super().__init__(...) does not work."""
        self.__init__(xyz=em.xyz,
                          phot=em.phot,
                          frame_ix=em.frame_ix,
                          id=em.id,
                          prob=em.prob,
                          bg=em.bg,
                          xyz_cr=em.xyz_cr,
                          phot_cr=em.phot_cr,
                          bg_cr=em.bg_cr,
                          sanity_check=True)

    def _sanity_check(self):
        """
        Tests the integrity of the EmitterSet
        :return: None
        """
        if not same_shape_tensor(0, self.xyz, self.phot, self.frame_ix, self.id, self.bg,
                                 self.xyz_cr, self.phot_cr, self.bg_cr):
            raise ValueError("Coordinates, photons, frame ix, id and prob are not of equal shape in 0th dimension.")

        if not same_dim_tensor(torch.ones(1), self.phot, self.prob, self.frame_ix, self.id):
            raise ValueError("Expected photons, probability frame index and id to be 1D.")

    def __str__(self):
        print_str = f"EmitterSet" \
                    f"\n::num emitters: {self.num_emitter}"

        if self.num_emitter == 0:
            print_str += "\n::frame range: n.a." \
                         "\n::spanned volume: n.a."
        else:
            print_str += f"\n::frame range: {self.frame_ix.min().item()} - {self.frame_ix.max().item()}" \
                         f"\n::spanned volume: {self.xyz.min(0)[0].numpy()} - {self.xyz.max(0)[0].numpy()}"
        return print_str

    def __eq__(self, other):
        """
        Two emittersets are equal if all of it's (tensor) members are equal within a certain float precision
        :param other: EmitterSet
        :return: (bool)
        """
        is_equal = True
        is_equal *= tutil.tens_almeq(self.xyz, other.xyz, self.eq_precision)
        is_equal *= tutil.tens_almeq(self.frame_ix, other.frame_ix, self.eq_precision)
        is_equal *= tutil.tens_almeq(self.phot, other.phot, self.eq_precision)
        # is_equal *= tutil.tens_almeq(self.id, other.id, self.eq_precision)  # this is in question
        is_equal *= tutil.tens_almeq(self.prob, other.prob, self.eq_precision)

        if torch.isnan(self.bg).all():
            is_equal *= torch.isnan(other.bg).all()
        else:
            is_equal *= tutil.tens_almeq(self.bg, other.bg, self.eq_precision)
        if torch.isnan(self.xyz_cr).all():
            is_equal *= torch.isnan(other.xyz_cr).all()
        else:
            is_equal *= tutil.tens_almeq(self.bg, other.bg, self.eq_precision)
        if torch.isnan(self.phot_cr).all():
            is_equal *= torch.isnan(other.phot_cr).all()
        else:
            is_equal *= tutil.tens_almeq(self.bg, other.bg, self.eq_precision)
        if torch.isnan(self.bg_cr).all():
            is_equal *= torch.isnan(other.bg_cr).all()
        else:
            is_equal *= tutil.tens_almeq(self.bg, other.bg, self.eq_precision)

        return is_equal.item()

    def clone(self):
        """
        Clone method to generate a deep copy.
        :return: Deep copy of self.
        """
        return EmitterSet(self.xyz.clone(),
                          self.phot.clone(),
                          self.frame_ix.clone(),
                          self.id.clone(),
                          self.prob.clone(),
                          self.bg.clone(),
                          self.xyz_cr.clone(),
                          self.phot_cr.clone(),
                          self.bg_cr.clone())

    @staticmethod
    def cat_emittersets(emittersets, remap_frame_ix=None, step_frame_ix=None):
        """
        Concatenates list of emitters and rempas there frame indices if they start over with 0 per item in list.

        :param emittersets: iterable of instances of this class
        :param remap_frame_ix: tensor of frame indices to which the 0th frame index in the emitterset corresponds to
        :param step_frame_ix: step of frame indices between items in list
        :return: emitterset
        """
        num_emittersets = emittersets.__len__()

        if remap_frame_ix is not None and step_frame_ix is not None:
            raise ValueError("You cannot specify remap frame ix and step frame ix at the same time.")
        elif remap_frame_ix is not None:
            shift = remap_frame_ix.clone()
        elif step_frame_ix is not None:
            shift = torch.arange(0, num_emittersets) * step_frame_ix
        else:
            shift = torch.zeros(num_emittersets)

        total_num_emitter = 0
        for i in range(num_emittersets):
            total_num_emitter += emittersets[i].num_emitter

        xyz = torch.cat([emittersets[i].xyz for i in range(num_emittersets)], 0)
        phot = torch.cat([emittersets[i].phot for i in range(num_emittersets)], 0)
        frame_ix = torch.cat([emittersets[i].frame_ix + shift[i] for i in range(num_emittersets)], 0)
        id = torch.cat([emittersets[i].id for i in range(num_emittersets)], 0)
        prob = torch.cat([emittersets[i].prob for i in range(num_emittersets)], 0)
        bg = torch.cat([emittersets[i].bg for i in range(num_emittersets)], 0)
        xyz_cr = torch.cat([emittersets[i].xyz_cr for i in range(num_emittersets)], 0)
        phot_cr = torch.cat([emittersets[i].phot_cr for i in range(num_emittersets)], 0)
        bg_cr = torch.cat([emittersets[i].bg_cr for i in range(num_emittersets)], 0)

        return EmitterSet(xyz, phot, frame_ix, id, prob, bg, xyz_cr, phot_cr, bg_cr)

    def sort_by_frame(self):
        self.frame_ix, ix = self.frame_ix.sort()
        self.xyz = self.xyz[ix, :]
        self.phot = self.phot[ix]
        self.id = self.id[ix]
        self.prob = self.prob[ix]
        self.bg = self.bg[ix]
        self.xyz_cr = self.xyz_cr[ix]
        self.phot_cr = self.phot_cr[ix]
        self.bg_cr = self.bg_cr[ix]

        self._sorted = True

    def get_subset(self, ix):
        return EmitterSet(self.xyz[ix, :], self.phot[ix], self.frame_ix[ix], self.id[ix], self.prob[ix], self.bg[ix],
                          self.xyz_cr[ix], self.phot_cr[ix], self.bg_cr[ix], sanity_check=False)

    def get_subset_frame(self, frame_start, frame_end, shift_to=None):
        """
        Get Emitterset for a certain frame range.
        Inclusive behaviour, so start and end are included.

        :param frame_start: (int)
        :param frame_end: (int)
        :shift_to: shift frame indices to a certain start value
        """

        ix = (self.frame_ix >= frame_start) * (self.frame_ix <= frame_end)
        em = self.get_subset(ix)
        if not shift_to:
            return em
        else:
            if em.num_emitter != 0:  # shifting makes only sense if we have an emitter.
                em.frame_ix = em.frame_ix - em.frame_ix.min() + shift_to
            return em

    @property
    def single_frame(self):
        return True if torch.unique(self.frame_ix).shape[0] == 1 else False

    def split_in_frames(self, ix_low=0, ix_up=None):
        """
        plit an EmitterSet into list of emitters (framewise).
        This calls C++ implementation torch_cpp for performance.
        If neither lower nor upper are inferred (via None values),
        output size will be a list of length (ix_up - ix_low + 1).
        If we have an empty set of emitters which we want to split, we get a one-element empty
        emitter 

        :param ix_low: (int) lower index, if None, use min value
        :param ix_up: (int) upper index, if None, use max value
        :return: list of instances of this class.
        """

        frame_ix, ix = self.frame_ix.sort()
        frame_ix = frame_ix.type(self.xyz.dtype)

        if self.id is not None:
            grand_matrix = torch.cat((self.xyz[ix, :],
                                      self.phot[ix].unsqueeze(1),
                                      frame_ix.unsqueeze(1),
                                      self.id[ix].unsqueeze(1),
                                      self.prob[ix].unsqueeze(1),
                                      self.bg[ix].unsqueeze(1),
                                      self.xyz_cr[ix, :],
                                      self.phot_cr[ix].unsqueeze(1),
                                      self.bg_cr[ix].unsqueeze(1)), dim=1)
        else:
            raise DeprecationWarning("No Id is not supported any more.")

        """The first frame is assumed to be 0. If it's negative go to the lowest negative."""
        if self.num_emitter != 0:
            ix_low_ = ix_low if ix_low is not None else frame_ix.min()
            ix_up_ = ix_up if ix_up is not None else frame_ix.max()

            # if not np.diff(frame_ix.numpy()) >= 0:
            #     raise ValueError("Array is not sorted even though it is supposed to be.")

            grand_matrix_list = torch_cpp.split_tensor(grand_matrix, frame_ix, ix_low_, ix_up_)

        else:
            """
            If there is absolutelty nothing to split (i.e. empty emitterset) we may want to have a list of
            empty sets of emitters. This only applies if ix_l is not inferred (i.e. -1).
            Otherwise we will have a one element list with an empty emitter set.
            """
            if ix_low is None:
                grand_matrix_list = [grand_matrix]
            else:
                grand_matrix_list = [grand_matrix] * (ix_up - ix_low + 1)
        em_list = []

        for i, em in enumerate(grand_matrix_list):
            em_list.append(EmitterSet(xyz=em[:, :3], phot=em[:, 3], frame_ix=em[:, 4], id=em[:, 5], prob=em[:, 6],
                                      bg=em[:, 7], xyz_cr=em[:, 8:11], phot_cr=em[:, 11], bg_cr=em[:, 12], sanity_check=False))

        return em_list

    def convert_coordinates(self, factor=None, shift=None, axis=None):
        """
        Convert coordinates. The order is factor -> shift -> axis
        :param factor: scale up factor
        :param shift: shift
        :param axis: permute axis
        :return:
        """
        xyz = self.xyz.clone()
        if factor is not None:
            if factor.size(0) == 2:
                factor = torch.cat((factor, torch.tensor([1.])), 0)

            xyz = xyz * factor.float().unsqueeze(0)
        if shift is not None:
            xyz += shift.float().unsqueeze(0)
        if axis is not None:
            xyz = xyz[:, axis]
        return xyz

    def convert_em(self, factor=None, shift=None, axis=None, frame_shift=0):
        """
        Convert a clone of the current emitter
        :param factor:
        :param shift:
        :param axis:
        :param frame_shift:
        :return:
        """

        emn = self.clone()
        emn.xyz = emn.convert_coordinates(factor, shift, axis)
        emn.frame_ix += frame_shift
        return emn

    def convert_em_(self, factor=None, shift=None, axis=None, frame_shift=0):
        """
        Inplace conversion of emitter
        :param factor:
        :param shift:
        :param axis:
        :param frame_shift:
        :return:
        """
        self.xyz = self.convert_coordinates(factor, shift, axis)
        self.frame_ix += frame_shift

    def write_to_csv(self, filename, model=None, comments=None, plain_header=False):
        """
        Write the prediction to a csv file. If shift, factor and axis are set, the order is shift->factor->axis.
        :param filename: output filename
        :param model: model file which was being used (will create a hash out of it)
        :param plain_header: uncomment the first line (which is where the column names are).
        :return:
        """
        grand_matrix = torch.cat((self.id.unsqueeze(1),
                                  self.frame_ix.unsqueeze(1),
                                  self.xyz,
                                  self.phot.unsqueeze(1),
                                  self.prob.unsqueeze(1),
                                  self.bg.unsqueeze(1),
                                  self.xyz_cr.unsqueeze(1),
                                  self.phot_cr.unsqueeze(1),
                                  self.bg_cr.unsqueeze(1)), 1)

        header = 'id, frame_ix, x, y, z, phot, prob, bg, xyz_cr, phot_cr, bg_cr\nThis is an export from DeepSMLM.\n' \
                 'Total number of emitters: {}'.format(self.num_emitter)

        if model is not None:
            if hasattr(model, 'hash'):
                header += '\nModel initialisation file SHA-1 hash: {}'.format(model.hash)

        if comments is not None:
            header += '\nUser comment during export: {}'.format(comments)
        np.savetxt(filename, grand_matrix.numpy(), delimiter=',', header=header)

        if plain_header:
            with open(filename, "r+") as f:
                content = f.read()  # read everything in the file
                content = content[2:]
                f.seek(0)  # rewind
                f.write(content)  # write the new line before

        return grand_matrix

    def write_csv_smap(self, filename, model=None, comments=None, factor=torch.tensor([100., 100., 1.]), shift=torch.tensor([50., -50., 0.]), axis=[1, 0, 2]):
        """
        Write to SMAP compatible csv (i.e. plain header for easier import in MATLAB)
        :param filename:
        :param mdoel:
        :param comments:
        :param factor:
        :param shift:
        :param axis:
        :return:
        """
        pseudo_em = self.clone()
        pseudo_em.convert_em_(factor, shift, axis, 1)

        if comments is None:
            comments = 'Export for SMAP'
        else:
            comments += '\nExport for SMAP'

        pseudo_em.write_to_csv(filename, model, comments, plain_header=True)

    @staticmethod
    def read_csv(filename):
        grand_matrix = np.loadtxt(filename, delimiter=",", comments='#')
        grand_matrix = torch.from_numpy(grand_matrix).float()
        if grand_matrix.size(1) == 7:
            return EmitterSet(xyz=grand_matrix[:, 2:5], frame_ix=grand_matrix[:, 1],
                              phot=grand_matrix[:, 5], id=grand_matrix[:, 0],
                              prob=grand_matrix[:, 6])
        else:
            return EmitterSet(xyz=grand_matrix[:, 2:5], frame_ix=grand_matrix[:, 1],
                              phot=grand_matrix[:, 5], id=grand_matrix[:, 0])

    def populate_crlb(self, psf):
        """
        Calculate the CRLB
        :return:
        """
        if self.num_emitter == 0:
            return

        em_split = self.split_in_frames(self.frame_ix.min(), self.frame_ix.max())
        for em in em_split:
            crlb, _ = psf.crlb(em.xyz, em.phot, em.bg, crlb_order='xyzpb')
            em.xyz_cr = crlb[:, :3]
            em.phot_cr = crlb[:, 3]
            em.bg_cr = crlb[:, 4]

        self._inplace_replace(self.cat_emittersets(em_split))


class RandomEmitterSet(EmitterSet):
    """
    A helper calss when we only want to provide a number of emitters.
    """
    def __init__(self, num_emitters, extent=32):
        """

        :param num_emitters:
        """
        xyz = torch.rand((num_emitters, 3)) * extent
        super().__init__(xyz, torch.ones_like(xyz[:, 0]), torch.zeros_like(xyz[:, 0]))

    def _inplace_replace(self, em):
        super().__init__(xyz=em.xyz,
                         phot=em.phot,
                         frame_ix=em.frame_ix,
                         id=em.id,
                         prob=em.prob,
                         bg=em.bg,
                         xyz_cr=em.xyz_cr,
                         phot_cr=em.phot_cr,
                         bg_cr=em.bg_cr,
                         sanity_check=False)


class CoordinateOnlyEmitter(EmitterSet):
    """
    A helper class when we only want to provide xyz, but not photons and frame_ix.
    Useful for testing. Photons will be tensor of 1, frame_ix tensor of 0.
    """
    def __init__(self, xyz):
        """

        :param xyz: (torch.tensor) N x 2, N x 3
        """
        super().__init__(xyz, torch.ones_like(xyz[:, 0]), torch.zeros_like(xyz[:, 0]))


class EmptyEmitterSet(CoordinateOnlyEmitter):
    """An empty emitter set."""
    def __init__(self):
        super().__init__(torch.zeros((0, 3)))

    def _inplace_replace(self, em):
        raise NotImplementedError("Inplace not yet implemented.")


class LooseEmitterSet:
    """
    An emitterset where we don't specify the frame_ix of an emitter but rather it's (real) time when
    it's starts to blink and it's ontime and then construct the EmitterSet (framewise) out of it.
    """
    def __init__(self, xyz, intensity, id=None, t0=None, ontime=None):
        """

        :param xyz: Coordinates
        :param phot: Photons
        :param intensity: Intensity (i.e. photons per time)
        :param id: ID
        :param t0: Timepoint of first occurences
        :param ontime: Duration in frames how long the emitter is on.
        """

        """If no ID specified, give them one."""
        if id is None:
            id = torch.arange(xyz.shape[0])

        self.xyz = xyz
        self.phot = None
        self.intensity = intensity
        self.id = id
        self.t0 = t0
        self.te = None
        self.ontime = ontime

    def return_emitterset(self):
        """
        Returns an emitter set

        :return: Instance of EmitterSet class.
        """
        xyz_, phot_, frame_ix_, id_ = self.distribute_framewise_py()
        return EmitterSet(xyz_, phot_, frame_ix_, id_)

    def distribute_framewise_py(self):
        """
        Distribute the emitters with arbitrary starting point and intensity over the frames so as to get a proper
        set of emitters (instance of EmitterSet) with photons.
        :return:
        """
        frame_start = torch.floor(self.t0)
        self.te = self.t0 + self.ontime  # endpoint
        frame_last = torch.ceil(self.te)
        frame_dur = (frame_last - frame_start).type(torch.LongTensor)

        num_emitter_brut = frame_dur.sum()

        xyz_ = torch.zeros(num_emitter_brut, self.xyz.shape[1])
        phot_ = torch.zeros_like(xyz_[:, 0])
        frame_ix_ = torch.zeros_like(phot_)
        id_ = torch.zeros_like(frame_ix_)

        c = 0
        for i in range(self.xyz.shape[0]):
            for j in range(frame_dur[i]):
                xyz_[c, :] = self.xyz[i]
                frame_ix_[c] = frame_start[i] + j
                id_[c] = self.id[i]

                """Calculate time on frame and multiply that by the intensity."""
                ontime_on_frame = torch.min(self.te[i], frame_ix_[c] + 1) - torch.max(self.t0[i], frame_ix_[c])
                phot_[c] = ontime_on_frame * self.intensity[i]

                c += 1
        return xyz_, phot_, frame_ix_, id_

    def _inplace_replace(self, em):
        raise NotImplementedError("Inplace not yet implemented.")


if __name__ == '__main__':
    num_emitter = 25000
    xyz = torch.rand((num_emitter, 3))
    phot = torch.ones_like(xyz[:, 0])
    t0 = torch.rand((num_emitter,)) * 10 - 1
    ontime = torch.rand((num_emitter,)) * 1.5

    LE = LooseEmitterSet(xyz, phot, None, t0, ontime)
    E = LE.return_emitterset()

    frame_ix = torch.zeros_like(xyz[:,0])
    em = EmitterSet(xyz, phot, frame_ix)
    em_splitted = em.split_in_frames(0, 0)

    print("Pseudo-Test successfull.")
