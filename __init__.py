from . import models
from . import report


def post_init_hook(env):
    """Crea la secuencia del Quedan de cada empresa existente (idempotente)."""
    env['res.company'].with_context(active_test=False).search([])._supplier_quedan_ensure_sequence()
