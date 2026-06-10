from .local.venvs.solver.lib.python3.13.site-packages.torch.testing._internal.optests.aot_autograd import outputs_msg
"""
how should the API layer for the user look like for OpenSesame python?
"""


# 1. how should I install models
# - a. CLI
# - b. singleton/scripted
# - c. auto (defaults)

"""CLI
uvx opensesame download vision --model ...
uvx opensesame download ocr --model ...
uvx opensesame download audio --model ...
uvx opensesame check
"""



# What features must OpenSesame have?
#
# noVNC and VNC support via VoidCrawl no-docker and docker support
    # This will lead to capability with a custom localhost web page for managing manual solving of challenges

# micro service architecture (redis queue) # Future V2 approach for production
#
#

# 2. Scripted approach
#
# Consult yosoi and VoidCrawl APi desing, very pydantic heavy; only if needed
# rich-click CLI UI is cleaner

from opensesame import Solver # top level import (can be stubbed from a different module)
from opensesame.policies import ... # policies == config but more declaritive (see yosoi IaC inspiration in ys.policys)



slvr = Solver(mode= [f"manual, auto"]) # default to auto, manual is where the noVNC, VNC web app with notifications and queue, auto is attemtping offline models first then escalates to failed queue w/ timeout

## 2.1 have manual per type solver
#
Solver.v2captcha_callback(...)

# Solver object can call

Solver.solve() # to auto solve w/

# considered args, record?,

solved_obj = await Solver.solve(type=Literal[str]:["v2captcha_callback, v2_invisible"])

print(solved_obj)

"""
outputs,

input_token
solved_token,
metadata
type,
timinig
etc. etc.
"""

# open sesame can yes


## Open Questions... Concurrency?
## What systems can we crack?
## how to build data flywheel for offline fine-tuning for smaller more efficient models? FUTURE FTURE
## System requirements and suggestions (nvidia, intel, amd, discrete vs. integrated and non-GPU w/ CPU. Embedded support and accuracy fall off)
