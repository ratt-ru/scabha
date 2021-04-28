from collections import OrderedDict
import re
import dataclasses

from omegaconf.omegaconf import OmegaConf
from omegaconf.errors import ConfigAttributeError
import pydantic
import pydantic.dataclasses

from .exceptions import ParameterValidationError, SchemaError
from typing import *

class File(str):
    pass

class Directory(str):
    pass

class MS(Directory):
    pass

class Error(str):
    pass

@dataclasses.dataclass
class Unresolved(object):
    value: str

    def __str__(self):
        return f"Unresolved({self.value})"

def validate_schema(schema: Dict[str, Any]):
    """Checks a set of parameter schemas for internal consistency.

    Args:
        schema (Dict[str, Any]):   dict of parameter schemas

    Raises:
        SchemaError: [description]
    """

    pass



def validate_parameters(params: Dict[str, Any], schema: Dict[str, Any], 
                        subst: Optional[Dict[str, Any]] = None,
                        defaults: Optional[Dict[str, Any]] = None,
                        ignore_unknowns = False, output=False
                        ) -> Dict[str, Any]:
    """Validates a dict of parameter values against a given schema 

    Args:
        params (Dict[str, Any]):   map of input parameter values
        schema (Dict[str, Any]):   map of parameter names to schemas. Each schema must contain a dtype field and a choices field.
        subst  (Dict[str, Any], optional): dictionary of substitutions to be made in str-valued parameters (using .format(**subst))
                                 if missing, str-valued parameters with {} in them will be marked as Unresolved.
        defaults (Dict[str, Any], optional): dictionary of default values to be used when a value is missing
        ignore_unknowns (bool):    if False, then params missing from the schema will raise a validation error

    Raises:
        ParameterValidationError: [description]
        SchemaError: [description]
        ParameterValidationError: [description]

    Returns:
        Dict[str, Any]: validated dict of parameters

    TODO:
        add options to propagate all errors out (as values of type Error) in place of exceptions?
    """
    # check for unknowns
    if not ignore_unknowns:
        for name in params:
            if name not in schema:
                raise ParameterValidationError(f"unknown parameter {name}")
        
    inputs = dict(**params)

    # omegaconf's DictConfig objects don't support derived types such as validation.Error, so for the purpose of
    # substitutions, convert the params into a regular dict first
    inputs = dict(**params)
    defaults = defaults or {}
    unresolved = {}

    # add missing defaults and/or implicit parameters
    for name, parmdef in schema.items():
        if name in inputs:
            if parmdef.implicit is not None:
                raise ParameterValidationError(f"implicit parameter {name} was supplied explicitly")
        else:
            if parmdef.implicit is not None:
                if name in defaults:
                    raise SchemaError(f"implicit parameter {name} also has a default value")
                inputs[name] = parmdef.implicit  ## TODO: move implcits to Stimela
            elif name in defaults:
                inputs[name] = defaults[name]

    # do substitutions if asked to
    # since substitutions can potentially reference each other, repeat this until things sette
    if subst is not None:
        # substitution namespace is input dict plus current parameter values
        subst1 = subst.copy()
        subst1_self = subst1['self'] = OmegaConf.create(inputs)
        for i in range(10):
            changed = False
            # loop over parameters and find ones to substitute
            for name, value in inputs.items():
                if isinstance(value, str) and not isinstance(value, Error):
                    try:
                        newvalue = value.format(**subst1)
                        subst1_self[name] = str(newvalue)
                    except ConfigAttributeError as exc:
                        newvalue = Error(f"ERR ({exc.key})")
                        subst1_self[name] = f"ERR ({exc.key})"
                    except Exception as exc:
                        newvalue = Error(f"{exc}")
                        subst1_self[name] = "ERR"
                    if newvalue != value:
                        inputs[name] = newvalue
                        changed = True
            if not changed:
                break 
        else:
            raise ParameterValidationError("recursion limit exceeded while evaluating {}-substitutions. This is usally caused by cyclic (cross-)references.")
    # else check for substitutions and swap in "unresolved" objects
    else:
        for name, value in list(inputs.items()):
            if isinstance(value, str) and not isinstance(value, Error) and re.search("{[^{]", value):
                unresolved[name] = Unresolved(value)
                del inputs[name]

    # create dataclass from parameter schema
    fields = []
    for name, parmdef in schema.items():
        if name in inputs:
            try:
                dtype = eval(parmdef.dtype, globals())
            except Exception as exc:
                raise SchemaError(f"invalid {name}.dtype={parmdef.dtype}")
            fields.append((name, dtype))

    dcls = dataclasses.make_dataclass("Parameters", fields)
    
    # convert this to a pydantic dataclass which does validation
    pcls = pydantic.dataclasses.dataclass(dcls)

    # validate
    try:   
        validated = pcls(**{name: value for name, value in inputs.items() if name in schema})
    except pydantic.ValidationError as exc:
        raise ParameterValidationError(f"{exc}")

    ## TODO: check "choices" field
    ## TODO: check File, Directory and MS typs, if asked to

    validated = dataclasses.asdict(validated)

    # add in unresolved values
    validated.update(**unresolved)

    return validated
