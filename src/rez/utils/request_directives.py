
from rez.vendor.six import six
from rez.vendor.schema.schema import Schema, Optional, Use, And
from rez.utils.formatting import PackageRequest
from rez.utils.data_utils import cached_property


basestring = six.string_types[0]


def filter_directive_requires(data):
    _directives = dict()

    def extract_directive(request):
        for Directive in _directive_classes:
            directive = Directive.create(request)
            if directive is not None:
                break
        else:
            # not a directive request
            return request

        request_ = directive.get_pre_build_request()
        _directives[request_.name] = directive

        return str(request_)

    # visit requires with schema
    #
    filtering_schema = And(basestring, Use(extract_directive))

    requires_filtering_schema = Schema({
        Optional("requires"):               [filtering_schema],
        Optional("build_requires"):         [filtering_schema],
        Optional("private_build_requires"): [filtering_schema],
        Optional("variants"):               [[filtering_schema]],
    })

    validated = _validate_partial(requires_filtering_schema, data)
    data.update(validated)

    return data, _directives


def evaluate_directive_requires(variant, build_context):
    package = variant.parent

    def evaluate_directive(request):
        request = PackageRequest(request)
        directive = package.directives.get(request.name)
        if directive:
            request = directive.get_post_build_request(build_context)
        return str(request)

    evaluation_schema = And(basestring, Use(evaluate_directive))

    requires_evaluation_schema = Schema({
        Optional("requires"):               [evaluation_schema],
        Optional("build_requires"):         [evaluation_schema],
        Optional("private_build_requires"): [evaluation_schema],
        Optional("variants"):               [[evaluation_schema]],
    })

    validated = _validate_partial(requires_evaluation_schema, package.data)
    # print(variant.variant_requires)
    # print("-", package.repository.data[package.name][str(package.version)])
    # package.repository.data[package.name][str(package.version)].update(validated)
    # print("+", package.repository.data[package.name][str(package.version)])
    # cached_property.uncache(package.resource, "handle")
    # cached_property.uncache(package.resource, "_data")
    # cached_property.uncache(variant, "parent")
    # package.resource._load()
    # # package.data.update(validated)
    # print(variant.variant_requires)
    # for key, value in validated.items():
    #     if key == "variants":
    #         print("-", package.data[key][variant.index])
    #         package.data[key][variant.index][:] = value[variant.index]
    #         print("+", package.data[key][variant.index])
    #     else:
    #         package.data[key][:] = value
        # if key == "variants":
        #     setattr(variant.resource, "variant_requires", value[variant.index])

    return validated


def _validate_partial(schema, data):
    s = schema._schema
    partial_data = dict()

    for key in s:
        key_name = key
        while isinstance(key_name, Schema):
            key_name = key_name._schema
        if isinstance(key_name, str):
            value = data.get(key_name)
            if value is not None:
                partial_data[key_name] = value

    return schema.validate(partial_data)


# directives
#

class DirectiveBase(object):
    """Base class of directive request handler"""

    def __init__(self, request_str):
        self.request = PackageRequest(request_str)

    @classmethod
    def name(cls):
        return None

    @classmethod
    def create(cls, request):
        raise NotImplementedError

    def get_pre_build_request(self):
        return self.request

    def get_post_build_request(self, build_context):
        """Format arguments to directive syntax string"""
        raise NotImplementedError


class HardenDirective(DirectiveBase):
    """Harden directive request version to specific rank"""

    def __init__(self, request_str, rank=None):
        super(HardenDirective, self).__init__(request_str)
        self._can_harden_request()
        self.rank = rank

    @classmethod
    def name(cls):
        return "harden"

    @classmethod
    def create(cls, request_str):
        if "//" not in request_str:
            return

        request_, d_str = request_str.split("//", 1)
        rank = None
        name = cls.name()

        if d_str == name or d_str.startswith(name + "("):

            arg_str = d_str[len(name):]
            if arg_str:
                rank = int(arg_str[1:-1].strip())

            return cls(request_, rank=rank)

    def _can_harden_request(self):
        pass

    def get_post_build_request(self, build_context):
        pkg_name = self.request.name
        variant = build_context.get_resolved_package(pkg_name)
        request_str = "%s-%s" % (pkg_name, variant.version.trim(self.rank))
        return PackageRequest(request_str)


_directive_classes = (
    HardenDirective,
)
