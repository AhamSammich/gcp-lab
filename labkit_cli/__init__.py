"""The lab workflows behind ./labkit.

The repository-local launcher calls the workflows directly.

labkit_cli.common - what start and reset genuinely share: repository
                   paths, the terminal toolkit, and the managed-lab
                   target boundary
labkit_cli.start  - scaffold a new lab, or refresh an existing one
labkit_cli.reset  - tear a finished lab back down, and the strict
                   credentials-only cleanup
"""
