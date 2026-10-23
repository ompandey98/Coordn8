import numpy as np

from tinyclf.model import init_params, loss_and_grads


def test_gradients_have_parameter_shapes():
    p = init_params(8, 1)
    x = np.zeros((4, 2), dtype=np.float32)
    _, g = loss_and_grads(p, x, np.array([0, 1, 0, 1]))
    assert all(g[k].shape == p[k].shape for k in p)
