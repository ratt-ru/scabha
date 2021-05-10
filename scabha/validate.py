from collections import OrderedDict
import re
import dataclasses
import os.path, glob

from omegaconf import OmegaConf, ListConfig, DictConfig, MISSING
from omegaconf.errors import ConfigAttributeError
import pydantic
import pydantic.dataclasses

from .exceptions import ParameterValidationError, SchemaError
from typing import *

class File(str):
    pass

class Directory(File):
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


def join_quote(values):
    return "'" + "', '".join(values) + "'" if values else ""


def validate_schema(schema: Dict[str, Any]):
    """Checks a set of parameter schemas for internal consistency.

    Args:
        schema (Dict[str, Any]):   dict of parameter schemas

    Raises:
        SchemaError: [description]
    """

    pass



def validate_parameters(params: Dict[str, Any], schemas: Dict[str, Any], 
                        subst: Optional[Dict[str, Any]] = None,
                        defaults: Optional[Dict[str, Any]] = None,
                        check_unknowns=True,    
                        check_required=True,
                        check_exist=True
                        ) -> Dict[str, Any]:
    """Validates a dict of parameter values against a given schema 

    Args:
        params (Dict[str, Any]):   map of input parameter values
        schema (Dict[str, Any]):   map of parameter names to schemas. Each schema must contain a dtype field and a choices field.
        subst  (Dict[str, Any], optional): dictionary of substitutions to be made in str-valued parameters (using .format(**subst))
                                 if missing, str-valued parameters with {} in them will be marked as Unresolved.
        defaults (Dict[str, Any], optional): dictionary of default values to be used when a value is missing

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
    if check_unknowns:
        for name in params:
            if name not in schemas:
                raise ParameterValidationError(f"unknown parameter '{name}'")
        
    # split inputs into unresolved substitutions, and proper inputs
    inputs = {name: value for name, value in params.items() if type(value) is not Unresolved}
    unresolved = {name: value for name, value in params.items() if type(value) is Unresolved}
    defaults = defaults or {}

    # add missing defaults 
    for name, schema in schemas.items():
        if name not in inputs:
            if name in defaults:
                inputs[name] = defaults[name]
            elif schema.default is not None:
                inputs[name] = schema.default


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
    # else check for substitutions and move them to the unresolved dict
    else:
        for name, value in list(inputs.items()):
            if isinstance(value, str) and not isinstance(value, Error) and re.search("{[^{]", value):
                unresolved[name] = Unresolved(value)
                del inputs[name]

    # check that required args are present
    if check_required:
        missing = [name for name, schema in schemas.items() if schema.required and name not in inputs and name not in unresolved]
        if missing:
                raise ParameterValidationError(f"missing required parameters: {join_quote(missing)}")

    # create dataclass from parameter schema
    validated = {}
    dtypes = {}
    fields = []
    for name, schema in schemas.items():
        if name in inputs:
            try:
                dtypes[name] = dtype_impl = eval(schema.dtype, globals())
            except Exception as exc:
                raise SchemaError(f"invalid {name}.dtype = {schema.dtype}")
            fields.append((name, dtype_impl))
            
            # OmegaConf dicts/lists need to be converted to standard contrainers for pydantic to take them
            if isinstance(inputs[name], (ListConfig, DictConfig)):
                inputs[name] = OmegaConf.to_container(inputs[name])

    dcls = dataclasses.make_dataclass("Parameters", fields)

    # convert this to a pydantic dataclass which does validation
    pcls = pydantic.dataclasses.dataclass(dcls)

    # check Files etc. and expand globs
    for name, value in inputs.items():
        # get schema from those that need validation, skip if not in schemas
        schema = schemas.get(name)
        if schema is None:
            continue
        # skip errors
        if isinstance(value, Error):
            continue
        dtype = dtypes[name]

        is_file = dtype in (File, Directory, MS)
        is_file_list = dtype in (List[File], List[Directory], List[MS])

        if is_file or is_file_list:
            # match to existing file(s)
            if type(value) is str:
                files = glob.glob(value)
            elif type(value) in (list, tuple):
                files = value
            else:
                raise ParameterValidationError(f"{name}: invalid type '{type(value)}'")

            if not files:
                if schema.required and check_exist:
                    raise ParameterValidationError(f"{name}: nothing matches '{value}'")
                else:
                    inputs[name] = [] if is_file_list else ""
                    continue

            # check for single file/dir
            if dtype in (File, Directory, MS):
                if len(files) > 1:
                    raise ParameterValidationError(f"{name}: multiple matches to '{value}'")
                if dtype is File:
                    if not os.path.isfile(files[0]):
                        raise ParameterValidationError(f"{name}: '{value}' is not a regular file")
                else:
                    if not os.path.isdir(files[0]):
                        raise ParameterValidationError(f"{name}: '{value}' is not a directory")
                inputs[name] = files[0]

            # else make list
            else:
                if dtype is List[File]:
                    if not all(os.path.isfile(f) for f in files):
                        raise ParameterValidationError(f"{name}: '{value}' matches non-files")
                else:
                    if not all(os.path.isdir(f) for f in files):
                        raise ParameterValidationError(f"{name}: '{value}' matches non-directories")
                inputs[name] = files

    # validate
    try:   
        validated = pcls(**{name: value for name, value in inputs.items() if name in schemas})
    except pydantic.ValidationError as exc:
        errors = [f"'{'.'.join(err['loc'])}': {err['msg']}" for err in exc.errors()]
        raise ParameterValidationError(', '.join(errors))

    validated = dataclasses.asdict(validated)

    # check choice-type parameters
    for name, value in validated.items():
        schema = schemas[name]
        if schema.choices and value not in schema.choices:
            raise ParameterValidationError(f"{name}: invalid value '{value}'")


    # add in unresolved values
    validated.update(**unresolved)

    ## TODO: check "choices" field


    return validated
