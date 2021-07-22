import sputnik_backend as backend
import torch


def _sparse_transpose(x):
    shape = x[0].clone()
    shape[0] = x[0][1]
    shape[1] = x[0][0]
    return (shape,) + x[1:]


def _transpose_helper(x, transpose):
    if isinstance(x, torch.Tensor):
        return x.t() if transpose else x
    if transpose:
        x = _sparse_transpose(x)
    return x + (transpose,)


def _wrap(x):
    if isinstance(x, torch.Tensor):
        return (x,)
    return x


def _is_transposed(x):
    return (not x.is_contiguous() and
            x.stride()[0] == 1 and
            x.stride()[1] == x.size()[0])


def _call_helper(op, out, a, b, trans_a, trans_b):
    args = (_wrap(_transpose_helper(a, trans_a)) +
            _wrap(_transpose_helper(b, trans_b)))
    if isinstance(out, tuple):
        args = args + out
    return op(*args)


def _preprocess_inputs(lhs, rhs, dy):
    if isinstance(lhs, torch.Tensor) and _is_transposed(lhs):
        lhs = lhs.t()
    if isinstance(rhs, torch.Tensor) and _is_transposed(rhs):
        rhs = rhs.t()
    if (isinstance(dy, torch.Tensor) and
        not dy.is_contiguous() and
        not _is_transposed(dy)):
        dy = dy.contiguous()
    if isinstance(dy, tuple) and not dy[1].is_contiguous():
        dy = (dy[0], dy[1].contiguous()) + dy[2:]
    return lhs, rhs, dy


def _postprocess_outputs(x, transpose, grad):
    if isinstance(x, torch.Tensor) and transpose:
        return grad.t()
    return grad


def _lhs_gradient(op, lhs, rhs, dy, trans_lhs, trans_rhs):
    lhs, rhs, dy = _preprocess_inputs(lhs, rhs, dy)

    a, b = (rhs, dy) if trans_lhs else (dy, rhs)
    trans_a = trans_lhs and trans_rhs
    trans_b = trans_lhs or not trans_rhs
    out = _call_helper(op, lhs, a, b, trans_a, trans_b)
    return _postprocess_outputs(lhs, trans_lhs, out)


def _rhs_gradient(op, lhs, rhs, dy, trans_lhs, trans_rhs):
    lhs, rhs, dy = _preprocess_inputs(lhs, rhs, dy)

    a, b = (dy, lhs) if trans_rhs else (lhs, dy)
    trans_a = not trans_lhs or trans_rhs
    trans_b = trans_lhs and trans_rhs
    out = _call_helper(op, rhs, a, b, trans_a, trans_b)
    return _postprocess_outputs(rhs, trans_rhs, out)


class DSD(torch.autograd.Function):

    @staticmethod
    def forward(ctx,
                shape,
                data,
                offsets,
                indices,
                transpose_a,
                rhs):
        ctx.save_for_backward(shape, data, offsets, indices, rhs)
        ctx.transpose_a = transpose_a
        out = torch.empty((shape[0], rhs.size()[1]),
                          dtype=rhs.dtype,
                          device=rhs.device)
        backend.dsd(shape,
                    data,
                    offsets,
                    indices,
                    transpose_a,
                    rhs,
                    out)
        return out

    @staticmethod
    def backward(ctx, dy):
        shape, data, offsets, indices, rhs = ctx.saved_tensors
        lhs = (shape, data, offsets, indices)
        trans_a = ctx.transpose_a
        trans_b = _is_transposed(rhs)

        ddata = None
        if ctx.needs_input_grad[1]:
            ddata = _lhs_gradient(sdd,
                                  lhs,
                                  rhs,
                                  dy,
                                  trans_a,
                                  trans_b)
        drhs = None
        if ctx.needs_input_grad[5]:
            op = dds if trans_b else dsd
            drhs = _rhs_gradient(op,
                                 lhs,
                                 rhs,
                                 dy,
                                 trans_a,
                                 trans_b)
        return None, ddata, None, None, None, drhs


dsd = DSD.apply


class DDS(torch.autograd.Function):

    @staticmethod
    def forward(ctx,
                lhs,
                shape,
                data,
                offsets,
                indices,
                transpose_b):
        ctx.save_for_backward(lhs, shape, data, offsets, indices)
        ctx.transpose_b = transpose_b
        out = torch.empty((lhs.size()[0], shape[1]),
                          dtype=lhs.dtype,
                          device=lhs.device)
        backend.dds(lhs,
                    shape,
                    data,
                    offsets,
                    indices,
                    transpose_b,
                    out)
        return out

    @staticmethod
    def backward(ctx, dy):
        lhs, shape, data, offsets, indices = ctx.saved_tensors
        rhs = (shape, data, offsets, indices)
        trans_a = _is_transposed(lhs)
        trans_b = ctx.transpose_b

        dlhs = None
        if ctx.needs_input_grad[0]:
            op = dsd if trans_a else dds
            dlhs = _lhs_gradient(op,
                                 lhs,
                                 rhs,
                                 dy,
                                 trans_a,
                                 trans_b)
        ddata = None
        if ctx.needs_input_grad[2]:
            ddata = _rhs_gradient(sdd,
                                  lhs,
                                  rhs,
                                  dy,
                                  trans_a,
                                  trans_b)
        return dlhs, None, ddata, None, None, None


dds = DDS.apply


class SDD(torch.autograd.Function):

    @staticmethod
    def forward(ctx,
                lhs,
                rhs,
                shape,
                data,
                offsets,
                indices):
        ctx.save_for_backward(lhs, rhs, shape, data, offsets, indices)
        out = torch.empty_like(data)
        backend.sdd(lhs,
                    rhs,
                    shape,
                    out,
                    offsets,
                    indices)
        return out

    @staticmethod
    def backward(ctx, dy):
        lhs, rhs, shape, data, offsets, indices = ctx.saved_tensors
        dy = (shape, dy, offsets, indices)
        trans_a = _is_transposed(lhs)
        trans_b = _is_transposed(rhs)

        dlhs = None
        if ctx.needs_input_grad[0]:
            op = dds if trans_a else dsd
            dlhs = _lhs_gradient(op,
                                 lhs,
                                 rhs,
                                 dy,
                                 trans_a,
                                 trans_b)
        drhs = None
        if ctx.needs_input_grad[1]:
            op = dsd if trans_b else dds
            drhs = _rhs_gradient(op,
                                 lhs,
                                 rhs,
                                 dy,
                                 trans_a,
                                 trans_b)
        return dlhs, drhs, None, None, None, None


sdd = SDD.apply


class SSD(torch.autograd.Function):

    @staticmethod
    def forward(ctx,
                lhs_shape,
                lhs_data,
                lhs_offsets,
                lhs_indices,
                transpose_a,
                rhs,
                shape,
                data,
                offsets,
                indices):
        ctx.save_for_backward(lhs_shape,
                              lhs_data,
                              lhs_offsets,
                              lhs_indices,
                              rhs,
                              shape,
                              data,
                              offsets,
                              indices)
        ctx.transpose_a = transpose_a
        out = torch.empty_like(data)
        backend.ssd(lhs_shape,
                    lhs_data,
                    lhs_offsets,
                    lhs_indices,
                    transpose_a,
                    rhs,
                    shape,
                    out,
                    offsets,
                    indices)
        return out

    @staticmethod
    def backward(ctx, dy):
        lhs = ctx.saved_tensors[:4]
        rhs = ctx.saved_tensors[4]
        dy = (ctx.saved_tensors[5], dy) + ctx.saved_tensors[7:]
        trans_a = ctx.transpose_a
        trans_b = _is_transposed(rhs)

        dlhs = None
        if ctx.needs_input_grad[1]:
            op = sds if trans_a else ssd
            dlhs = _lhs_gradient(op,
                                 lhs,
                                 rhs,
                                 dy,
                                 trans_a,
                                 trans_b)
        drhs = None
        if ctx.needs_input_grad[5]:
            drhs = _rhs_gradient(dss,
                                 lhs,
                                 rhs,
                                 dy,
                                 trans_a,
                                 trans_b)
        return None, dlhs, None, None, None, drhs, None, None, None, None


ssd = SSD.apply

class SDS(torch.autograd.Function):

    @staticmethod
    def forward(ctx,
                lhs,
                rhs_shape,
                rhs_data,
                rhs_offsets,
                rhs_indices,
                transpose_b,
                shape,
                data,
                offsets,
                indices):
        ctx.save_for_backward(lhs,
                              rhs_shape,
                              rhs_data,
                              rhs_offsets,
                              rhs_indices,
                              shape,
                              data,
                              offsets,
                              indices)
        ctx.transpose_b = transpose_b
        out = torch.empty_like(data)
        backend.sds(lhs,
                    rhs_shape,
                    rhs_data,
                    rhs_offsets,
                    rhs_indices,
                    transpose_b,
                    shape,
                    out,
                    offsets,
                    indices)
        return out

    @staticmethod
    def backward(ctx, dy):
        lhs = ctx.saved_tensors[0]
        rhs = ctx.saved_tensors[1:5]
        dy = (ctx.saved_tensors[5], dy) + ctx.saved_tensors[7:]
        trans_a = _is_transposed(lhs)
        trans_b = ctx.transpose_b

        dlhs = None
        if ctx.needs_input_grad[0]:
            dlhs = _lhs_gradient(dss,
                                 lhs,
                                 rhs,
                                 dy,
                                 trans_a,
                                 trans_b)
        drhs = None
        if ctx.needs_input_grad[2]:
            op = ssd if trans_b else sds
            drhs = _rhs_gradient(op,
                                 lhs,
                                 rhs,
                                 dy,
                                 trans_a,
                                 trans_b)
        return dlhs, None, drhs, None, None, None, None, None, None, None


sds = SDS.apply


class DSS(torch.autograd.Function):

    @staticmethod
    def forward(ctx,
                lhs_shape,
                lhs_data,
                lhs_offsets,
                lhs_indices,
                transpose_a,
                rhs_shape,
                rhs_data,
                rhs_offsets,
                rhs_indices,
                transpose_b):
        ctx.save_for_backward(lhs_shape,
                              lhs_data,
                              lhs_offsets,
                              lhs_indices,
                              rhs_shape,
                              rhs_data,
                              rhs_offsets,
                              rhs_indices)
        ctx.transpose_a = transpose_a
        ctx.transpose_b = transpose_b
        out = torch.empty((lhs_shape[0], rhs_shape[1]),
                          dtype=lhs_data.dtype,
                          device=lhs_data.device)
        backend.dss(lhs_shape,
                    lhs_data,
                    lhs_offsets,
                    lhs_indices,
                    transpose_a,
                    rhs_shape,
                    rhs_data,
                    rhs_offsets,
                    rhs_indices,
                    transpose_b,
                    out)
        return out

    @staticmethod
    def backward(ctx, dy):
        lhs = ctx.saved_tensors[:4]
        rhs = ctx.saved_tensors[4:]
        trans_a = ctx.transpose_a
        trans_b = ctx.transpose_b

        dlhs = None
        if ctx.needs_input_grad[1]:
            op = ssd if trans_a else sds
            dlhs = _lhs_gradient(op,
                                 lhs,
                                 rhs,
                                 dy,
                                 trans_a,
                                 trans_b)
        drhs = None
        if ctx.needs_input_grad[6]:
            op = sds if trans_b else ssd
            drhs = _rhs_gradient(op,
                                 lhs,
                                 rhs,
                                 dy,
                                 trans_a,
                                 trans_b)
        return None, dlhs, None, None, None, None, drhs, None, None, None, None


dss = DSS.apply