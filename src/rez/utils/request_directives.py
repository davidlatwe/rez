
from rez.vendor.version.version import VersionRange
from rez.vendor.version.requirement import Requirement
from rez.vendor.version.util import dewildcard
from rez.utils.formatting import PackageRequest
from copy import copy
import inspect
import sys


# directives
#

class DirectiveBase(object):
    @classmethod
    def name(cls):
        """Return the name of the directive"""
        raise NotImplementedError

    def parse(self, arg_string):
        """Parse arguments from directive syntax string"""
        raise NotImplementedError

    def to_string(self, args):
        """Format arguments to directive syntax string"""
        raise NotImplementedError

    def process(self, range_, version, rank=None):
        """Process requirement's version range"""
        raise NotImplementedError


class DirectiveHarden(DirectiveBase):
    @classmethod
    def name(cls):
        return "harden"

    def parse(self, arg_string):
        if arg_string:
            return [int(arg_string[1:-1].strip())]
        return []

    def to_string(self, args):
        if args and args[0]:
            return "%s(%d)" % (self.name(), args[0])
        return self.name()

    def process(self, range_, version, rank=None):
        if rank:
            version = version.trim(rank)
        hardened = VersionRange.from_version(version)
        new_range = range_.intersection(hardened)
        return new_range


# helpers
#

def parse_directive(request):
    if "//" in request:
        request_, directive = request.split("//", 1)
        # TODO: ranking needed.
    elif "*" in request:
        request_, directive = _convert_wildcard_to_directive(request)
        if not directive:
            return request
    else:
        return request

    # parse directive and save into anonymous inventory
    _directive_args = directive_manager.parse(directive)
    directive_manager.loaded.put(_directive_args,
                                 key=request_,
                                 anonymous=True)

    return request_


def bind_directives(package):
    """
    Open anonymous space
    Pour directives into anonymous space while package schema validating
    Move directives into identified space after package data validated
    """
    directive_manager.loaded.commit(key=package)


def apply_directives(variant):
    directed_requires = directive_manager.processed.retrieve(key=variant)

    # just like how `cached_property` caching attributes, override
    # requirement attributes internally. These change will be picked
    # up by `variant.parent.validated_data`.
    for key, value in directed_requires.items():
        # requires, build_requires, private_build_requires
        setattr(variant.parent.resource, key, value)


def process_directives(variant, context):
    """
    1. collect requires from variant
    2. retrieve directives from inventory for each require of variant
    3. match directives with context resolved packages
    4. pass resolved package versions and directive to expansion manager
    """
    # retrieve directives
    directives = directive_manager.loaded.retrieve(key=variant) or dict()

    processed = dict()
    resolved_packages = {p.name: p for p in context.resolved_packages}
    attributes = [
        "requires",
        "build_requires",
        "private_build_requires",
        # "variants",  # this needs special care
    ]
    for attr in attributes:
        changed_requires = []
        has_directive = False

        # MOVE THIS TO ANOTHER FUNCTION
        for request in getattr(variant, attr, None) or []:
            directive = directives.get(str(request))
            package = resolved_packages.get(request.name)

            if directive and package:
                has_directive = True
                name, args = directive

                new_range = directive_manager.process(
                    request.range,
                    package.version,
                    name,
                    args,
                )
                new_req = Requirement.construct(package.name, new_range)
                request = PackageRequest(str(new_req))

            changed_requires.append(request)

        if has_directive:
            processed[attr] = changed_requires

    directive_manager.processed.put(processed, key=variant)


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


class DirectiveManager(object):

    def __init__(self):
        self._loaded = PackageDataInventory()
        self._processed = PackageDataInventory()
        self._handlers = dict()

    @property
    def loaded(self):
        return self._loaded

    @property
    def processed(self):
        return self._processed

    def register_handler(self, cls, name=None, *args, **kwargs):
        name = name or cls.name()
        self._handlers[name] = cls(*args, **kwargs)

    def parse(self, string):
        for name, handler in self._handlers.items():
            if string == name or string.startswith(name + "("):
                return name, handler.parse(string[len(name):])

    def to_string(self, name, args):
        handler = self._handlers[name]
        return handler.to_string(args)

    def process(self, range_, version, name, args):
        handler = self._handlers[name]
        return handler.process(range_, version, *args)


class PackageDataInventory(object):

    def __init__(self):
        self._anonymous = dict()
        self._identified = dict()

    def _storage(self, anonymous):
        return self._anonymous if anonymous else self._identified

    def _hash(self, key, anonymous):
        if anonymous:
            return key
        else:
            package = key
            return (
                package.name,
                str(package.version),
                package.uuid,
            )

    def commit(self, key):
        key = self._hash(key, anonymous=False)
        self._identified[key] = self._anonymous.copy()
        self._anonymous.clear()

    def put(self, data, key, anonymous=False):
        key = self._hash(key, anonymous)
        storage = self._storage(anonymous)
        storage[key] = data

    def retrieve(self, key, anonymous=False):
        key = self._hash(key, anonymous)
        storage = self._storage(anonymous)
        if key in storage:
            return copy(storage[key])

    def drop(self, key, anonymous=False):
        key = self._hash(key, anonymous)
        storage = self._storage(anonymous)
        if key in storage:
            storage.pop(key)


def anonymous_directive_string(request):
    """Test use"""
    name, args = directive_manager.loaded.retrieve(request, anonymous=True)
    return directive_manager.to_string(name, args)


directive_manager = DirectiveManager()

# Auto register all subclasses of DirectiveBase in this module
for obj in list(sys.modules[__name__].__dict__.values()):
    if not inspect.isclass(obj):
        continue
    if issubclass(obj, DirectiveBase) and obj is not DirectiveBase:
        directive_manager.register_handler(obj)
