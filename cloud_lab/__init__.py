"""The lab workflows behind ./labkit.

The repository-local launcher calls the workflows directly.

cloud_lab.common - what start and reset genuinely share: repository
                   paths, the terminal toolkit, and the managed-lab
                   target boundary
cloud_lab.start  - scaffold a new lab, or refresh an existing one
cloud_lab.reset  - tear a finished lab back down, and the strict
                   credentials-only cleanup
"""
