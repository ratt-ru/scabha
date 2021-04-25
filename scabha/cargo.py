import os.path, re
from typing import Any, List, Dict, Optional, Union
from enum import Enum
from dataclasses import dataclass, field
from omegaconf.omegaconf import MISSING, OmegaConf
from collections import OrderedDict

from .exceptions import CabValidationError, DefinitionError
from . import validate
from .validate import validate_parameters

## almost supported by omegaconf, see https://github.com/omry/omegaconf/issues/144, for now just use Any
ListOrString = Any   

def EmptyDictDefault():
    return field(default_factory=lambda:OrderedDict())

def EmptyListDefault():
    return field(default_factory=lambda:[])


Conditional = Optional[str]


@dataclass 
class ParameterPolicies(object):
    # if true, value is passed as a positional argument, not an option
    positional: Optional[bool] = None
    # for list-type values, use this as a separator to paste them together. Use "list"
    # to repeat list-type values as multiple arguments
    repeat: Optional[str] = None
    # prefix for non-positional arguments
    prefix: Optional[str] = "--"

    # Value formatting policies.
    # If set, specifies {}-type format strings used to convert the value(s) to string(s).
    # For a non-list value:
    #   * if 'format_list' is set, formatts the value into a lisyt of strings as fmt[i].format(value, **dict)
    #     example:  ["{0}", "{0}"] will simply repeat the value twice
    #   * if 'format' is set, value is formatted as format.format(value, **dict) 
    # For a list-type value:
    #   * if 'format_list' is set, each element #i formatted separately as fmt[i].format(*value, **dict)
    #     example:  ["{0}", "{1}"] will simply 
    #   * if 'format' is set, each element #i is formatted as format.format(value[i], **dict) 
    # **dict contains all parameters passed to a cab, so these can be used in the formatting
    format: Optional[str] = None
    format_list: Optional[List[str]] = None


@dataclass 
class CabManagement:        # defines common cab management behaviours
    environment: Optional[Dict[str, str]] = EmptyDictDefault()
    cleanup: Optional[Dict[str, ListOrString]]     = EmptyDictDefault()   
    wranglers: Optional[Dict[str, ListOrString]]   = EmptyDictDefault()   


@dataclass
class Parameter(object):
    """Parameter (of cab or recipe)"""
    info: str = ""
    # for input parameters, this flag indicates a read-write (aka input-output aka mixed-mode) parameter e.g. an MS
    writeable: bool = False
    # data type
    dtype: str = "str"
    # for file-type parameters, specifies that the filename is implicitly set inside the step (i.e. not a free parameter)
    implicit: Optional[str] = None
    # optonal list of arbitrary tags, used to group parameters
    tags: List[str] = EmptyListDefault()

    # if True, parameter is required
    required: bool = False

    # choices for an option-type parameter (should this be List[str]?)
    choices:  Optional[List[Any]] = ()

    # inherited from Stimela 1 -- used to handle paremeters inside containers?
    # might need a re-think, but we can leave them in for now  
    alias: Optional[str] = ""
    pattern: Optional[str] = MISSING

    policies: ParameterPolicies = ParameterPolicies()

@dataclass
class Cargo(object):
    name: Optional[str] = None                    # cab name. (If None, use image or command name)
    info: Optional[str] = None                    # description
    inputs: Dict[str, Parameter] = EmptyDictDefault()
    outputs: Dict[str, Parameter] = EmptyDictDefault()
    defaults: Dict[str, Any] = EmptyDictDefault()

    def __post_init__(self):
        for name in self.inputs.keys():
            if name in self.outputs:
                raise DefinitionError(f"{name} appears in both inputs and outputs")
        self.params = {}
        self._inputs_outputs = None
        # pausterized name
        self.name_ = re.sub(r'\W', '_', self.name or "")  # pausterized name

    @property
    def inputs_outputs(self):
        if self._inputs_outputs is None:
            self._inputs_outputs = self.inputs.copy()
            self._inputs_outputs.update(**self.outputs)
        return self._inputs_outputs
    
    @property
    def invalid_params(self):
        return [name for name, value in self.params.items() if type(value) is validate.Error]

    @property
    def missing_params(self):
        return {name: schema for name, schema in self.inputs_outputs.items() if schema.required and name not in self.params}

    def finalize(self, config, full_name=None, log=None):
        pass

    def validate(self, config, params: Optional[Dict[str, Any]] = None, subst: Optional[Dict[str, Any]] = None):
        pass

    def update_parameter(self, name, value):
        self.params[name] = value

    def make_substitition_namespace(self):
        ns = {name: str(value) for name, value in self.params.items()}
        ns.update(**{name: "MISSING" for name in self.missing_params})
        return OmegaConf.create(ns)


@dataclass 
class Cab(Cargo):
    """Represents a cab i.e. an atomic task in a recipe.
    See dataclass fields below for documentation of fields.

    Additional attributes available after validation with arguments:

        self.input_output:      combined parameter dict (self.input + self.output), maps name to Parameter
        self.missing_params:    dict (name to Parameter) of required parameters that have not been specified
    
    Raises:
        CabValidationError: [description]
    """
    image: Optional[str] = None                   # container image to run 
    command: str = MISSING                        # command to run (inside or outside the container)
    # not sure what these are
    msdir: Optional[bool] = False
    prefix: Optional[str] = "-"
    # cab management and cleanup definitions
    management: CabManagement = CabManagement()

    policies: ParameterPolicies = ParameterPolicies()

    def __post_init__ (self):
        if self.name is None:
            self.name = self.image or self.command.split()[0]
        Cargo.__post_init__(self)
        for param in self.inputs.keys():
            if param in self.outputs:
                raise CabValidationError(f"cab {self.name}: parameter {param} is both an input and an output, this is not permitted")

    def validate(self, config, params: Optional[Dict[str, Any]] = None, subst: Optional[Dict[str, Any]] = None):
        self.params = validate_parameters(params, self.inputs_outputs, defaults=self.defaults, subst=subst)

    @property
    def summary(self):
        lines = [f"cab {self.name}:"] 
        for name, value in self.params.items():
            # if type(value) is validate.Error:
            #     lines.append(f"  {name} = ERR: {value}")
            # else:
            lines.append(f"  {name} = {value}")
                
        lines += [f"  {name} = ???" for name in self.missing_params.keys()]
        return lines


    def run(self):
        if self.image:
            raise RuntimeError("container runner not yet implemented")
        else:
            import scabha
            from scabha import proc_utils
            proc_utils.build_cab_arguments()
        

