#!/usr/bin/env python3
"""Root-own ONLY a staged identity overlay, with the HFS+ catalog patcher.

The generic hfs-rootown.py next to this tool re-owns everything the image's
builder created (uid 501) — that is the shared-cache rule. An identity staging
pass must not touch anything else: this wrapper injects the documented root
uid, clears the keep-prefix exception list, and restricts edits to
/private/etc and /private/var/db/dslocal, so thousands of pre-existing
mount-litter/cryptex records are never rewritten. Usage is identical:
hfs-rootown-identity.py IMG [--apply].

The original pass missed records allocated in the current catalog leaf chain
(the older all-node scanner reported "would patch 0" while an owners-enabled
mount showed 501:20); hfs-fixmeta.py's offset-array/leaf-chain parser is the
authoritative one, and hfs-identity-meta.py is the check gate for these paths.
"""
import importlib.util
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / 'hfs-rootown.py'
spec = importlib.util.spec_from_file_location('identity_hfs_rootown', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.ROOT_UID = 0
module.KEEP_PREFIXES = ()
TARGETS = ('/private/etc', '/private/var/db/dslocal')
original_hidden_check = module.is_hidden_path

def outside_identity_scope(path):
    candidate = path.rstrip('/')
    in_scope = any(candidate == target or candidate.startswith(target + '/')
                   for target in TARGETS)
    return not in_scope or original_hidden_check(path)

module.is_hidden_path = outside_identity_scope
module.main()
