
from rez.vendor.version.version import VersionRange
from rez.vendor.version.requirement import Requirement
from rez.vendor.version.util import dewildcard
from rez.utils.formatting import PackageRequest


# directives
#

class DirectiveBase(object):
    pass


class DirectiveHarden(DirectiveBase):
    name = "harden"

    def parse(self, arg_string):
        if arg_string:
            return [int(arg_string[1:-1].strip())]
        return []

    def to_string(self, args):
        if args and args[0]:
            return "%s(%d)" % (self.name, args[0])
        return self.name

    def process(self, range_, version, rank=None):
        if rank:
            version = version.trim(rank)
        hardened = VersionRange.from_version(version)
        new_range = range_.intersection(hardened)
        return new_range


# helpers
#

class RequestExpansionManager(object):

    def __init__(self):
        self._handlers = dict()

    def register_handler(self, cls, name=None, *args, **kwargs):
        name = name or cls.name
        self._handlers[name] = cls(*args, **kwargs)

    def parse(self, string):
        for name, expander in self._handlers.items():
            if string == name or string.startswith(name + "("):
                return name, expander.parse(string[len(name):])

    def to_string(self, name, args):
        expander = self._handlers[name]
        return expander.to_string(args)

    def process(self, range_, version, name, args):
        expander = self._handlers[name]
        return expander.process(range_, version, *args)


def register_expansion_handler(cls, name=None, manager=None, *args, **kwargs):
    manager = manager or _request_expansion_manager
    manager.register_handler(cls, name, *args, **kwargs)


_request_expansion_manager = RequestExpansionManager()
register_expansion_handler(DirectiveHarden)
# TODO: auto register all subclasses of DirectiveBase in this module


def collect_directive_requires(package):
    """
    Open anonymous space
    Pour directives into anonymous space while package schema validating
    Move directives into identified space after package data validated
    """
    handle = _InventoryHandle()
    handle.set_package(package)
    _identified_directives.check_in(handle.key)


def apply_expanded_requires(variant):
    handle = _InventoryHandle()
    handle.set_package(variant)
    expanded_requires = _expanded_requirements.retrieve(handle.key)

    # just like how `cached_property` caching attributes, override
    # requirement attributes internally. These change will be picked
    # up by `variant.parent.validated_data`.
    for key, value in expanded_requires.items():
        # requires, build_requires, private_build_requires
        setattr(variant.parent.resource, key, value)


def expand_requires(variant, context):
    """
    1. collect requires from variant
    2. retrieve directives from inventory for each require of variant
    3. match directives with context resolved packages
    4. pass resolved package versions and directive to expansion manager
    """
    # retrieve directives
    handle = _InventoryHandle()
    handle.set_package(variant)
    directives = _identified_directives.retrieve(handle.key)

    expanded = dict()
    resolved_packages = {p.name: p for p in context.resolved_packages}
    attributes = [
        "requires",
        "build_requires",
        "private_build_requires",
        # "variants",  # this needs special care
    ]
    for attr in attributes:
        changed_requires = []
        has_expansion = False

        for requirement in getattr(variant, attr, None) or []:
            directive = directives.get(str(requirement))
            package = resolved_packages.get(requirement.name)

            if directive and package:
                has_expansion = True
                name, args = directive

                requirement = PackageRequest(str(Requirement.construct(
                    name=package.name,
                    range=_request_expansion_manager.process(
                        requirement.range,
                        package.version,
                        name,
                        args,
                    )
                )))

            changed_requires.append(requirement)

        if has_expansion:
            expanded[attr] = changed_requires

    handle = _InventoryHandle()
    handle.set_package(variant)
    _expanded_requirements.put(handle.key, expanded)


class DirectiveRequestParser(object):

    @classmethod
    def parse(cls, request):
        """parse requirement expansion directive"""

        if "//" in request:
            request_, directive = request.split("//", 1)
        elif "*" in request:
            request_, directive = _convert_wildcard_to_directive(request)
            if not directive:
                return request
        else:
            return request

        # parse directive and save into anonymous inventory
        directive_args = _request_expansion_manager.parse(directive)
        _anonymous_directives[request_] = directive_args

        return request_


def _convert_wildcard_to_directive(request):
    ranks = dict()

    with dewildcard(request) as deer:
        req = deer.victim

        def ranking(version, rank_):
            wild_ver = deer.restore(str(version))
            ranks[wild_ver] = rank_
        deer.on_version(ranking)

    cleaned_request = str(req)
    # do some cleanup
    cleaned_request = deer.restore(cleaned_request)

    if len(ranks) > 1:
        rank = next(v for k, v in ranks.items() if "*" in k)
    else:
        rank = next(iter(ranks.values()))

    if rank < 0:
        directive = "harden"
    else:
        directive = "harden(%d)" % rank

    return cleaned_request, directive


# Database, internal use
#

class _Inventory(object):

    def __init__(self):
        self._storage = dict()

    def put(self, key, data):
        self._storage[key] = data

    def retrieve(self, key):
        if key not in self._storage:
            return dict()
        return self._storage[key].copy()

    def drop(self, key):
        self._storage.pop(key)


class _DirectiveInventory(_Inventory):

    def check_in(self, key):
        self._storage[key] = _anonymous_directives.copy()
        _anonymous_directives.clear()


class _InventoryHandle(object):

    def __init__(self):
        self.key = None

    def set_package(self, package):
        self.key = (
            package.name,
            str(package.version),
            package.uuid,
        )


def anonymous_directive_string(request):
    """Test use"""
    name, args = _anonymous_directives.get(request)
    return _request_expansion_manager.to_string(name, args)


_anonymous_directives = dict()
_identified_directives = _DirectiveInventory()
_expanded_requirements = _Inventory()
