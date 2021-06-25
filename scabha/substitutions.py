import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .validate import Error
from .exceptions import SubstitutionError


# thanks to https://gist.github.com/bgusach/a967e0587d6e01e889fd1d776c5f3729
def multireplace(string, replacements, ignore_case=False):
    """
    Given a string and a replacement map, it returns the replaced string.
    :param str string: string to execute replacements on
    :param dict replacements: replacement dictionary {value to find: value to replace}
    :param bool ignore_case: whether the match should be case insensitive
    :rtype: str
    """
    # If case insensitive, we need to normalize the old string so that later a replacement
    # can be found. For instance with {"HEY": "lol"} we should match and find a replacement for "hey",
    # "HEY", "hEy", etc.
    if ignore_case:
        def normalize_old(s):
            return s.lower()

        re_mode = re.IGNORECASE

    else:
        def normalize_old(s):
            return s

        re_mode = 0

    replacements = {normalize_old(key): val for key, val in replacements.items()}
    
    # Place longer ones first to keep shorter substrings from matching where the longer ones should take place
    # For instance given the replacements {'ab': 'AB', 'abc': 'ABC'} against the string 'hey abc', it should produce
    # 'hey ABC' and not 'hey ABc'
    rep_sorted = sorted(replacements, key=len, reverse=True)
    rep_escaped = map(re.escape, rep_sorted)
    
    # Create a big OR regex that matches any of the substrings to replace
    pattern = re.compile("|".join(rep_escaped), re_mode)
    
    # For each match, look up the new string in the replacements, being the key the normalized old string
    return pattern.sub(lambda match: replacements[normalize_old(match.group(0))], string)


class SubstitutionNamespace(OrderedDict):
    """Implements a namespace that can do {}-substitutions on itself
    """
    @dataclass
    class Properties(object):
        mutable: bool = True
        forgiving: bool = False
        updated: bool = False

    _default_prop_ = Properties()

    def __init__(self, **kw):
        """Initializes the namespace. Keywords are _add_'ed as items in the namespace
        """
        super().__setattr__('_props_', SubstitutionNamespace.Properties())
        super().__setattr__('_child_props_', {})
        super().__setattr__('_forgave_', set())
        SubstitutionNamespace._update_(self, **kw)

    def copy(self):
        newcopy = SubstitutionNamespace()
        OrderedDict.__setattr__(newcopy, '_props_', self._props_)
        OrderedDict.__setattr__(newcopy, '_child_props_', self._child_props_.copy())
        OrderedDict.__setattr__(newcopy, '_forgave_', self._forgave_.copy())
        for key, value in self.items():
            OrderedDict.__setitem__(newcopy, key, value)
        return newcopy

    def _update_(self, **kw):
        """Updates items in the namespace using _add_()
        """
        for name, value in kw.items():
            SubstitutionNamespace._add_(self, name, value)

    def _add_(self, k: str, v: Any, forgiving=False, mutable=True):
        """Adds an item to the namespace.

        Args:
            k (str): item key
            v (Any): item value. A dict or OrderedDict value becomes a SubstitutionNamespace automatically
            forgiving (bool, optional): If True, sub-namespace is "forgiving" with references to missing items,
                returning "(name)" for ns.name if name is missing. If False, such references result in an AttributeError.
                Default is False.
            mutable (bool, optional): If False, sub-namespace is immutable and not will not have substitutions done inside it. Defaults to True.
        """
        props = SubstitutionNamespace.Properties(mutable=mutable, forgiving=forgiving)
        if type(v) in (dict, OrderedDict):
            v = SubstitutionNamespace(**v)
        if isinstance(v, SubstitutionNamespace):
            OrderedDict.__setattr__(v, '_props_', props)
        self._child_props_[k] = props
        super().__setitem__(k, v)

    def __setattr__(self, name: str, value: Any) -> None:
        SubstitutionNamespace._add_(self, name, value)

    def __setitem__(self, k: str, v: Any) -> None:
        SubstitutionNamespace._add_(self, k, v)

    def __getattr__(self, name: str) -> Any:
        if name in self:
            return super().get(name)
        elif self._props_.forgiving:
            self._forgave_.add(name)
            return f"({name})"
        else:
            raise AttributeError(name)

    def _substitute_(self, subst: Optional['SubstitutionNamespace'] = None):
        """Recursively substitutes {}-strings within this namespace

        Args:
            subst (SubstitutionNamespace, optional): Namespace used to look up substitutions. Defaults to self.

        Returns:
            SubstitutionNamespace, updated, unresolved: output namespace (same as self if copy=False), count of updates, count of unresolved substitutions
        """
        updated = unresolved = 0
        output = self
        # loop over parameters and find ones to substitute
        for name, value in super().items():
            props = self._child_props_[name]
            updated1 = unresolved1 = 0
            # substitute strings
            if isinstance(value, str) and not isinstance(value, Error) and "{" in value:
                # format string value
                try:
                    # protect "{{" and "}}" from getting converted to a single brace by pre-replacing them
                    newvalue = multireplace(value, {'{{': '\u00AB', '}}': '\u00BB'})
                    newvalue = newvalue.format(**(subst or output))
                    newvalue = multireplace(newvalue, {'\u00AB': '{{', '\u00BB': '}}'})
                    updated1 = int(value != newvalue)
                except Exception as exc:
                    newvalue = exc
                    unresolved1 = updated1 = 1
            # else substitute into mutable sub-namespaces
            elif isinstance(value, SubstitutionNamespace) and props.mutable:
                newvalue, updated1, unresolved1 = value._substitute_(subst or output)
            elif isinstance(value, Exception):
                unresolved1 = 1
            # has something changed? make copy of ourselves if so
            if updated1:
                if output is self:
                    output = self.copy()
                OrderedDict.__setitem__(output, name, newvalue)
            # update counters
            updated += updated1
            unresolved += unresolved1

        return output, updated, unresolved

    def _clear_forgivens_(self):
        self._forgave_ = set()
        for child in self.values():
            if isinstance(child, SubstitutionNamespace):
                child._clear_forgivens_()

    def _collect_forgivens_(self, name: Optional[str] = None):
        own_name = name or "."
        result = [f"{own_name}.{key}" for key in self._forgave_]
        for child_name, child in self.items():
            if isinstance(child, SubstitutionNamespace):
                result += child._collect_forgivens_(f"{name}.{child_name}" if name is not None else child_name)
        return result

    def _finalize_braces_(self):
        output = self
        for name, value in self.items():
            props = self._child_props_[name]
            updated = False
            if isinstance(value, SubstitutionNamespace) and props.mutable:
                newvalue = value._finalize_braces_()
                updated = newvalue is not value
            elif isinstance(value, str):
                newvalue = value.format()  # this converts {{ and }} to { and }
                updated = newvalue != value
            if updated:
                if output is self:
                    output = self.copy()
                OrderedDict.__setitem__(output, name, newvalue)
        return output

    def _print_(self, prefix="", printfunc=print):
        for name, value in self.items():
            if name.startswith("_") or name.endswith("_"):
                continue
            if isinstance(value, SubstitutionNamespace):
                printfunc(f"{prefix}{name}:")
                value._print_(prefix + "  ")
            elif isinstance(value, Exception):
                printfunc(f"{prefix}{name}: ERR: {value}")
            else:
                printfunc(f"{prefix}{name}: {value}")


def self_substitute(ns: SubstitutionNamespace, name: Optional[str] = None, debugprint = None):
    """Resolves {}-substitutions within a namespace.

    Args:
        ns (SubstitutionNamespace): namespace to do substitutions in.
        name (Optional[str], optional): name of this namespace, used in messages.
        debugprint (callable, optional): if set, function used to print debug messages.

    Raises:
        SubstitutionError: [description]

    Returns:
        SubstitutionNamespace: copy of namespace with substitutions in it. Will be the same as ns if no substitutions done
    """
    ns._clear_forgivens_()

    debugprint and debugprint("--- before substitution ---")
    debugprint and ns._print_(printfunc=debugprint, prefix="  ")

    # repeat as long as values keep changing, but qut after 10 cycles in case of infinite cross-refs
    for i in range(10):
        ns, updated, unresolved = ns._substitute_()
        debugprint and debugprint(f"--- iteration {i} updated {updated} unresolved {unresolved} ---")
        if not updated:
            break 
        debugprint and ns._print_(printfunc=debugprint, prefix="  ")
    else:
        raise SubstitutionError("recursion limit exceeded while evaluating {}-substitutions. This is usally caused by cyclic (cross-)substitutions.")

    # clear up "{{"s
    debugprint and debugprint(f"--- finalizing curly braces ---")
    ns = ns._finalize_braces_()
    debugprint and ns._print_(printfunc=debugprint, prefix="  ")

    return ns, unresolved, ns._collect_forgivens_(name)


# def copy_updates(src: SubstitutionNamespace, dest: Dict[str, Any]):
#     for name, value in src.items():
#         props = src._child_props_[name]
#         if props.updated:
#             dest[name] = value
