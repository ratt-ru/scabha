from collections import OrderedDict
import re
import dataclasses
import os, os.path, glob, yaml

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
                        defaults: Optional[Dict[str, Any]] = None,
                        check_unknowns=True,    
                        check_required=True,
                        check_exist=True,
                        expand_globs=True,
                        create_dirs=False
                        ) -> Dict[str, Any]:
    """Validates a dict of parameter values against a given schema 

    Args:
        params (Dict[str, Any]):   map of input parameter values
        schema (Dict[str, Any]):   map of parameter names to schemas. Each schema must contain a dtype field and a choices field.
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
        if inputs.get(name) is None:
            if name in defaults:
                inputs[name] = defaults[name]
            elif schema.default is not None:
                inputs[name] = schema.default

    # check that required args are present
    if check_required:
        missing = [name for name, schema in schemas.items() if schema.required and inputs.get(name) is None and name not in unresolved]
        if missing:
            raise ParameterValidationError(f"missing required parameters: {join_quote(missing)}")

    # create dataclass from parameter schema
    validated = {}
    dtypes = {}
    fields = []
    for name, schema in schemas.items():
        value = inputs.get(name)
        if value is not None:
            try:
                dtypes[name] = dtype_impl = eval(schema.dtype, globals())
            except Exception as exc:
                raise SchemaError(f"invalid {name}.dtype = {schema.dtype}")
            fields.append((name, dtype_impl))
            
            # OmegaConf dicts/lists need to be converted to standard contrainers for pydantic to take them
            if isinstance(value, (ListConfig, DictConfig)):
                inputs[name] = OmegaConf.to_container(value)

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
        if value is None or isinstance(value, Error):
            continue
        dtype = dtypes[name]

        is_file = dtype in (File, Directory, MS)
        is_file_list = dtype in (List[File], List[Directory], List[MS])

        # must this file exist? Schema may force this check, otherwise follow the default check_exist policy
        must_exist = check_exist if schema.must_exist is None else schema.must_exist

        if is_file or is_file_list:
            # match to existing file(s)
            if type(value) is str:
                # try to interpret string as a formatted list (a list substituted in would come out like that)
                try:
                    files = yaml.safe_load(value)
                    if type(files) is not list:
                        files = None
                except Exception as exc:
                    files = None
                # if not, fall back to treating it as a glob
                if files is None:
                    files = sorted(glob.glob(value)) if expand_globs else [value]
            elif type(value) in (list, tuple):
                files = value
            else:
                raise ParameterValidationError(f"'{name}': invalid type '{type(value)}'")

            if not files:
                if must_exist:
                    raise ParameterValidationError(f"'{name}={value}' does not specify any file(s)")
                else:
                    inputs[name] = [] if is_file_list else ""
                    continue

            # check for existence
            if must_exist: 
                not_exists = [f for f in files if not os.path.exists(f)]
                if not_exists:
                    raise ParameterValidationError(f"'{name}': {','.join(not_exists)} doesn't exist")

            # check for single file/dir
            if dtype in (File, Directory, MS):
                if len(files) > 1:
                    raise ParameterValidationError(f"'{name}': multiple files given ({value})")
                # check that files are files and dirs are dirs
                if os.path.exists(files[0]):
                    if dtype is File:
                        if not os.path.isfile(files[0]):
                            raise ParameterValidationError(f"'{name}': {value} is not a regular file")
                    else:
                        if not os.path.isdir(files[0]):
                            raise ParameterValidationError(f"'{name}': {value} is not a directory")
                inputs[name] = files[0]
                if create_dirs:
                    dirname = os.path.dirname(files[0])
                    if dirname:
                        os.makedirs(dirname, exist_ok=True)
            # else make list
            else:
                # check that files are files and dirs are dirs
                if dtype is List[File]:
                    if not all(os.path.isfile(f) for f in files if os.path.exists(f)):
                        raise ParameterValidationError(f"{name}: {value} matches non-files")
                else:
                    if not all(os.path.isdir(f) for f in files if os.path.exists(f)):
                        raise ParameterValidationError(f"{name}: {value} matches non-directories")
                inputs[name] = files
                if create_dirs:
                    for path in files:
                        dirname = os.path.dirname(path)
                        if dirname:
                            os.makedirs(dirname, exist_ok=True)

    # validate
    try:   
        validated = pcls(**{name: value for name, value in inputs.items() if name in schemas and value is not None})
    except pydantic.ValidationError as exc:
        errors = [f"'{'.'.join(err['loc'])}': {err['msg']}" for err in exc.errors()]
        raise ParameterValidationError(', '.join(errors))

    validated = dataclasses.asdict(validated)

    # check choice-type parameters
    for name, value in validated.items():
        schema = schemas[name]
        if schema.choices and value not in schema.choices:
            raise ParameterValidationError(f"{name}: invalid value '{value}'")

    # check for mkdir directives
    if create_dirs:
        for name, value in validated.items():
            if schemas[name].mkdir:
                dirname = os.path.dirname(value)
                if dirname:
                    os.makedirs(dirname, exist_ok=True)

    # add in unresolved values
    validated.update(**unresolved)

    return validated
