"""Temporary probe: env Var propagation and editable-install resolution."""

import os

print("MYPYPATH =", os.environ.get("MYPYPATH"))

import openharness
import openharness.rag.utils as rag_utils

print("openharness package =", openharness.__file__)
print("rag.utils module =", rag_utils.__file__)
