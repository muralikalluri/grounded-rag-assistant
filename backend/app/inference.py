"""One process-wide lock for all local torch inference (embedder + reranker).

Concurrent torch calls from several threads crashed the process in testing (segfault, and a
Metal command-buffer assertion on Apple GPUs when the two models ran at the same time). Local
inference is CPU/GPU-bound anyway, so it is serialised across BOTH models.
"""

import threading

INFERENCE_LOCK = threading.Lock()
