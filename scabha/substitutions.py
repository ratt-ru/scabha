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
    @dataclass
    class Properties(object):
        mutable: bool = False
        forgiving: bool = False
        updated: bool = False
        error: Optional[Exception] = None

    _default_prop_ = Properties()

    def __init__(self, **kw):
        super().__setattr__('_props_', SubstitutionNamespace.Properties())
        super().__setattr__('_child_props_', {})
        super().__setattr__('_forgave_', set())
        SubstitutionNamespace._update_(self, **kw)

    def _update_(self, **kw):
        for name, value in kw.items():
            SubstitutionNamespace._add_(self, name, value)

    def _add_(self, k: str, v: Any, forgiving=False, mutable=True):
        props = SubstitutionNamespace.Properties(mutable=mutable, forgiving=forgiving)
        if type(v) in (dict, OrderedDict):
            v = SubstitutionNamespace(**v)
        if isinstance(v, SubstitutionNamespace):
            OrderedDict.__setattr__(v, '_props_', props)
        self._child_props_[k] = props
        super().__setitem__(k, v)

    def _is_updated_(self, name):
        return name in self._child_props_ and self._child_props_[name].updated

    def _has_error_(self, name):
        return self._child_props_[name].error if name in self._child_props_ else None

    def _has_forgiven_(self, name):
        return self._forgave_

    def _clear_updated_(self):
        self._props_.updated = False
        self._props_.error = None
        self._forgave_.clear()
        for name, child in super().items():
            props = self._child_props_[name]
            props.updated = False
            props.error = None
            if isinstance(child, SubstitutionNamespace) and props.mutable:
                child._clear_updated_()

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
        updated = 0
        unresolved = 0
        subst = subst or self
        # loop over parameters and find ones to substitute
        for name, value in super().items():
            props = self._child_props_[name]
            if isinstance(value, str) and not isinstance(value, Error) and "{" in value:
                # format string value
                try:
                    # protect "{{" and "}}" from getting converted to a single brace by pre-replacing them
                    newvalue = multireplace(value, {'{{': '\u00AB', '}}': '\u00BB'})
                    newvalue = newvalue.format(**subst)
                    newvalue = multireplace(newvalue, {'\u00AB': '{{', '\u00BB': '}}'})
                except Exception as exc:
                    props.error = exc
                    unresolved += 1
                    continue
                if value != newvalue:
                    super().__setitem__(name, newvalue)
                    props.updated = True
                    updated += 1
            elif isinstance(value, SubstitutionNamespace) and props.mutable:
                updated1, unresolved1 = value._substitute_(subst)
                updated += updated1
                unresolved += unresolved1

        return updated, unresolved

    def _collect_forgivens_(self, name: Optional[str] = None):
        own_name = name or "."
        result = [f"{own_name}.{key}" for key in self._forgave_]
        for child_name, child in self.items():
            if isinstance(child, SubstitutionNamespace):
                result += child._collect_forgivens_(f"{name}.{child_name}" if name is not None else child_name)
        return result

    def _finalize_braces_(self):
        updated = False
        for name, value in self.items():
            props = self._child_props_[name]
            if isinstance(value, SubstitutionNamespace) and props.mutable:
                if value._finalize_braces_():
                    updated = props.updated = True
            elif isinstance(value, str) and props.error is None:
                newvalue = value.format()
                if newvalue != value:
                    super().__setitem__(name, newvalue)
                    updated = props.updated = True
        return True

    def _print_(self, prefix="", printfunc=print):
        for name, value in self.items():
            if isinstance(value, SubstitutionNamespace):
                printfunc(f"{prefix}{name}:")
                value._print_(prefix + "  ")
            else:
                printfunc(f"{prefix}{name}: {value}")

def self_substitute(ns: SubstitutionNamespace, name: Optional[str] = None, printfunc = None):
    # recursively clear the updated property
    ns._clear_updated_()
    any_updated = False

    printfunc and printfunc("--- before substitution ---")
    printfunc and ns._print_(printfunc=printfunc, prefix="  ")

    # repeat as long as values keep changing, but qut after 10 cycles in case of infinite cross-refs
    for i in range(10):
        updated, unresolved = ns._substitute_()
        printfunc and printfunc(f"--- iteration {i} updated {updated} unresolved {unresolved} ---")
        if updated:
            any_updated = True
        else:
            break 
        printfunc and ns._print_(printfunc=printfunc, prefix="  ")
    else:
        raise SubstitutionError("recursion limit exceeded while evaluating {}-substitutions. This is usally caused by cyclic (cross-)substitutions.")

    # clear up "{{"s
    printfunc and printfunc(f"--- finalizing curly braces ---")
    if ns._finalize_braces_():
        any_updated = True
    printfunc and ns._print_(printfunc=printfunc, prefix="  ")

    return any_updated, unresolved, ns._collect_forgivens_(name)


def copy_updates(src: SubstitutionNamespace, dest: Dict[str, Any]):
    for name, value in src.items():
        props = src._child_props_[name]
        if props.updated:
            dest[name] = value
