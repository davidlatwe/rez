
from rez.vendor.version.version import VersionRange
from rez.vendor.version.requirement import Requirement
from rez.vendor.version.util import dewildcard
from rez.utils.formatting import PackageRequest
from copy import copy


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

class DirectiveManager(object):

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


def register_directive(cls, name=None, manager=None, *args, **kwargs):
    manager = manager or _directive_manager
    manager.register_handler(cls, name, *args, **kwargs)


_directive_manager = DirectiveManager()
register_directive(DirectiveHarden)
# TODO: auto register all subclasses of DirectiveBase in this module


def collect_directive_requires(package):
    """
    Open anonymous space
    Pour directives into anonymous space while package schema validating
    Move directives into identified space after package data validated
    """
    _loaded_directives.commit(key=package)


def apply_directives(variant):
    directed_requires = _processed_directives.retrieve(key=variant)

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
    directives = _loaded_directives.retrieve(key=variant) or dict()

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

        for request in getattr(variant, attr, None) or []:
            directive = directives.get(str(request))
            package = resolved_packages.get(request.name)

            if directive and package:
                has_directive = True
                name, args = directive

                new_range = _directive_manager.process(
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

    _processed_directives.put(processed, key=variant)


class DirectiveRequestParser(object):

    @classmethod
    def parse(cls, request):
        """parse requirement expansion directive"""

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
        directive_args = _directive_manager.parse(directive)
        _loaded_directives.put(directive_args,
                               key=request_,
                               anonymous=True)

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
    name, args = _loaded_directives.retrieve(request, anonymous=True)
    return _directive_manager.to_string(name, args)


_loaded_directives = _Inventory()
_processed_directives = _Inventory()
