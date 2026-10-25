"""Convert XBlock's Ethereum phishing transaction network (MulDiGraph.pkl, Chen et al., ACM TOIT 2021) into
plain arrays that engine/public_replay.py reads. Run it once, outside the repo's data:

    .venv/bin/python -I engine/eth_phishing.py ~/Downloads/MulDiGraph.pkl ~/calhacks-sandbox/eth

The file is a pickled networkx MultiDiGraph, and a pickle can run code when it's loaded, so this never
calls a plain pickle.load: the unpickler below only hands out a stand-in class for the graph and a few
builtin containers, and refuses every other import the file asks for (an unexpected one stops the run
with its name). networkx itself is never imported. Run with -I so nothing next to the data file can be
imported either. Standard library only, apart from numpy scalars if the file contains them.

Writes to the output directory (nothing else):
- addresses.txt: one address per line; line n is node id n
- phishing.u8: 1 per node id that Etherscan labels phishing (the graph's `isp` attribute), else 0
- src.u32, dst.u32: node ids of each transaction's sender and receiver
- amount.f64: ETH moved; ts.f64: unix timestamp
"""

import builtins
import copyreg
import pickle
import sys
from array import array
from pathlib import Path

SAFE_BUILTINS = {"dict", "list", "set", "frozenset", "tuple", "int", "float", "str", "bytes", "bool", "complex", "object"}
GRAPH_CLASSES = {"Graph", "DiGraph", "MultiGraph", "MultiDiGraph"}


class Graph:
    """Stands in for networkx's graph classes: it only receives the pickled attributes (_node, _succ, ...)."""

    def __setstate__(self, state):
        self.__dict__.update(state)


class SafeUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if module.startswith("networkx.classes.") and name in GRAPH_CLASSES:
            return Graph
        if module == "builtins" and name in SAFE_BUILTINS:
            return getattr(builtins, name)
        if (module, name) == ("copyreg", "_reconstructor"):  # protocol 0/1 object rebuild; its class args pass through here too
            return copyreg._reconstructor
        if module in ("numpy.core.multiarray", "numpy._core.multiarray") and name == "scalar":
            import numpy.core.multiarray as m  # rebuilds one number from its raw bytes
            return m.scalar
        if module == "numpy" and name == "dtype":
            import numpy
            return numpy.dtype
        raise pickle.UnpicklingError(f"blocked an import the file asked for: {module}.{name}")


def convert(pkl: Path, out: Path) -> None:
    with pkl.open("rb") as f:
        g = SafeUnpickler(f).load()
    if not isinstance(g, Graph):
        raise SystemExit(f"expected a networkx graph, got {type(g).__name__}")
    nodes = g.__dict__.get("_node") or g.__dict__.get("node")
    succ = g.__dict__.get("_succ") or g.__dict__.get("succ") or g.__dict__.get("_adj")
    ids = {}
    phishing = array("B")
    out.mkdir(parents=True, exist_ok=True)
    with (out / "addresses.txt").open("w") as f:
        for address, attrs in nodes.items():
            ids[address] = len(ids)
            phishing.append(1 if attrs.get("isp") in (1, "1", True) else 0)
            f.write(f"{address}\n")
    src, dst, amount, ts = array("I"), array("I"), array("d"), array("d")
    for u, nbrs in succ.items():
        for v, edges in nbrs.items():
            for attrs in edges.values():
                src.append(ids[u])
                dst.append(ids[v])
                amount.append(float(attrs["amount"]))
                ts.append(float(attrs["timestamp"]))
    for name, values in (("phishing.u8", phishing), ("src.u32", src), ("dst.u32", dst), ("amount.f64", amount), ("ts.f64", ts)):
        with (out / name).open("wb") as f:
            values.tofile(f)
    print(f"{len(ids):,} addresses ({sum(phishing):,} phishing), {len(src):,} transactions -> {out}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    convert(Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser())
