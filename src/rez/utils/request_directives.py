
from rez.exceptions import PackageRequestError
from rez.vendor.version.version import VersionRange, Version
from rez.vendor.version.requirement import Requirement, VersionedObject
from rez.vendor.version.util import ranking, is_valid_bound
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

    def process(self, range_, version, rank=None):
        if rank:
            version.trim(rank)
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


def retrieve_directives(data):
    handle = _InventoryHandle(_identified_directives)
    handle.set_package(data)
    return _identified_directives.retrieve()


def apply_expanded_requires(variant):
    handle = _InventoryHandle(_expanded_requirements)
    handle.set_package(variant.validated_data())
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
    directives = retrieve_directives(variant.validated_data())
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
            directive_args = directives.get(str(requirement))
            package = resolved_packages.get(requirement.name)

            if directive_args and package:
                has_expansion = True

                # version = VersionedObject.construct(package.name, package.version)
                # print(directive_args, version, package.version)
                # requirement.range_ = VersionRange(str(package.version))
                # requirement._str = None
                requirement = PackageRequest(str(Requirement.construct(
                    name=package.name,
                    range=_request_expansion_manager.process(
                        requirement.range,
                        package.version,
                        *directive_args
                    )
                )))

            changed_requires.append(requirement)

        if has_expansion:
            expanded[attr] = changed_requires

    handle = _InventoryHandle(_expanded_requirements)
    handle.set_package(variant.validated_data())
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
        cleaned_versions = {}
        wildcard_map = {}
        request_ = request

        def clean_version(version):
            wildcard_found = False
            while version and str(version[-1]) in wildcard_map:
                token_ = wildcard_map[str(version[-1])]
                version = version.trim(len(version) - 1)

                if token_ == "**":
                    if wildcard_found:  # catches bad syntax '**.*'
                        return None
                    else:
                        wildcard_found = True
                        break

                wildcard_found = True

            if wildcard_found:
                return version

        def visit_version(version):
            for v, cleaned_v in cleaned_versions.items():
                if version == next(v):
                    return next(cleaned_v)

            version_ = clean_version(version)
            if version_ is None:
                return None

            cleaned_versions[version] = version_
            return version_

        # replace wildcards with valid version tokens that can be replaced again
        # afterwards. This produces a horrendous, but both valid and temporary,
        # version string.
        #
        while "**" in request_:
            uid = "_%s_" % uuid4().hex
            request_ = request_.replace("**", uid, 1)
            wildcard_map[uid] = "**"

        while "*" in request_:
            uid = "_%s_" % uuid4().hex
            request_ = request_.replace("*", uid, 1)
            wildcard_map[uid] = "*"

        req = Requirement(request_, invalid_bound_error=False)
        ranks = ranking(req.range)

        req.range_.visit_versions(visit_version)

        for bound in list(req.range_.bounds):
            if not is_valid_bound(bound):
                req.range_.bounds.remove(bound)

        if not req.range_.bounds:
            req.range_ = VersionRange()

        cleaned_request = str(req)

        # do some cleanup
        for uid, token in wildcard_map.items():
            cleaned_request = cleaned_request.replace(uid, token)
            for key in list(ranks):
                if uid in key:
                    clean_key = key.replace(uid, token)
                    ranks[clean_key] = -1 if "**" in token else ranks[key]
                    ranks.pop(key)

        if len(ranks) > 1:
            directive = None
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

    def set_package(self, data):
        # TODO: instead of asking validated data, pass in package and
        #   take package.name, package.version,.. directly.
        identifier = (
            data["name"],
            str(data.get("version", "")),
            data.get("uuid"),
        )
        self._inventory.select(identifier)


def anonymous_directive(request):
    """Test use"""
    return _anonymous_directives.get(request)


_lock = Lock()
_anonymous_directives = dict()
_identified_directives = _DirectiveInventory()
_expanded_requirements = _Inventory()
