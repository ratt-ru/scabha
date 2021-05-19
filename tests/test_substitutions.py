import pytest
from scabha.substitutions import SubstitutionNamespace, self_substitute
from omegaconf import OmegaConf 



def test_subst():

    x = OmegaConf.create()
    x.a = 1
    x.b = "{foo.a} not meant to be substituted!"
    x.c = 1

    ns = SubstitutionNamespace(foo=SubstitutionNamespace())

    bar = SubstitutionNamespace()
    ns._add_("x", x, mutable=False)
    ns._add_("bar", bar, forgiving=True)

    ns.foo.a = "{x.a}-{x.c}"
    ns.foo.b = "{foo.a}{{}}"
    ns.foo.c = "{bar.a}-{bar.x}-{bar.b}"

    ns.bar.a = 1
    ns.bar.b = "{foo.b}"
    ns.bar.c = "{foo.x} deliveberately unresolved"

    updated, unresolved, forgivens = self_substitute(ns, printfunc=print)

    print(f"forgivens are: {forgivens}")

    assert updated is True
    assert unresolved == 1
    assert len(forgivens) == 1
    assert ns.bar._has_error_('c')

if __name__ == "__main__":
    test_subst()

