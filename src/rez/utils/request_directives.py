
from rez.exceptions import PackageRequestError
from rez.vendor.version.version import VersionRange, Version
from rez.vendor.version.requirement import Requirement, VersionedObject
from rez.vendor.version.util import is_valid_bound, dewildcard
from rez.utils.formatting import PackageRequest
from contextlib import contextmanager
from threading import Lock
from uuid import uuid4
import re


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


@contextmanager
def collect_directive_requests():
    """
    Open anonymous space
    Pour directives into anonymous space while package schema validating
    Move directives into identified space after package data validated
    """
    handle = _InventoryHandle(_identified_directives)
    try:
        _lock.acquire()
        yield handle
        _identified_directives.check_in()
    finally:
        _lock.release()


def retrieve_directives(variant):
    handle = _InventoryHandle(_identified_directives)
    handle.set_package(variant)
    return _identified_directives.retrieve()


def apply_expanded_requires(variant):
    handle = _InventoryHandle(_expanded_requirements)
    handle.set_package(variant)
    # just like how `cached_property` caching attributes, override
    # requirement attributes internally. These change will be picked
    # up by `variant.parent.validated_data`.
    expanded_requires = _expanded_requirements.retrieve()
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
    expanded = dict()
    resolved_packages = {p.name: p for p in context.resolved_packages}
    directives = retrieve_directives(variant)
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

    handle = _InventoryHandle(_expanded_requirements)
    handle.set_package(variant)
    _expanded_requirements.put(expanded)


class DirectiveRequestParser(object):
    """
    Convertible:
        foo-**          -> foo//harden
        foo==**         -> foo//harden
        foo-1.**        -> foo-1//harden
        foo==1.*        -> foo-1//harden(2)
        foo>1.*         -> foo>1//harden(2)
        foo-1.*+        -> foo-1+//harden(2)
        foo>=1.*        -> foo>=1//harden(2)

    Unsupported:
        foo-1..2.*      -| multi-rank hardening
        foo-2.*,1       -| multi-rank hardening
        foo-1.*+<2.*.*  -| multi-rank hardening
        foo<**          -| harden undefined bound (doesn't make sense either)

    """
    directive_regex = re.compile(r"[-@#=<>.]\*(?!.*//)|//")
    unsupported_regex = re.compile(r"[|,]|\.{2}|[+<>]\*|\*[<>+].+")

    @classmethod
    def parse(cls, request):
        """parse requirement expansion directive"""

        if "//" in request:
            request_, directive = request.split("//", 1)
        elif "*" in request:
            request_, directive = cls.convert_wildcard_to_directive(request)
            if not directive:
                return request
        else:
            return request

        # TODO: parse directive and save, via manager
        directive_args = _request_expansion_manager.parse(directive)
        # save directive into anonymous inventory for now.
        _anonymous_directives[request_] = directive_args

        return request_

    @classmethod
    def convert_wildcard_to_directive(cls, request):
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
        self._key = None

    def select(self, identifier):
        if identifier not in self._storage:
            self._storage[identifier] = dict()
        self._key = identifier

    def put(self, data):
        self._storage[self._key] = data

    def retrieve(self):
        return self._storage[self._key].copy()

    def drop(self):
        self._storage.pop(self._key)


class _DirectiveInventory(_Inventory):

    def check_in(self):
        self._storage[self._key] = _anonymous_directives.copy()
        _anonymous_directives.clear()


class _InventoryHandle(object):

    def __init__(self, inventory):
        self._inventory = inventory

    def set_package(self, package):
        identifier = (
            package.name,
            str(package.version),
            package.uuid,
        )
        self._inventory.select(identifier)


def anonymous_directive_string(request):
    """Test use"""
    name, args = _anonymous_directives.get(request)
    return _request_expansion_manager.to_string(name, args)


_lock = Lock()
_anonymous_directives = dict()
_identified_directives = _DirectiveInventory()
_expanded_requirements = _Inventory()
