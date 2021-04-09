
from contextlib import contextmanager
from itertools import groupby
from uuid import uuid4


class VersionError(Exception):
    pass


class ParseException(Exception):
    pass


class _Common(object):
    def __str__(self):
        raise NotImplementedError

    def __ne__(self, other):
        return not (self == other)

    def __repr__(self):
        return "%s(%r)" % (self.__class__.__name__, str(self))


def dedup(iterable):
    """Removes duplicates from a sorted sequence."""
    for e in groupby(iterable):
        yield e[0]


def is_valid_bound(bound):
    lower_tokens = bound.lower.version.tokens
    upper_tokens = bound.upper.version.tokens
    return ((lower_tokens is None or lower_tokens)
            and (upper_tokens is None or upper_tokens))


def is_fixed_bound(bound):
    return (bound.lower == bound.upper
            or next(bound.lower.version) == bound.upper.version)


def ranking(version_range):
    ranks = dict()
    for bound in version_range.bounds:
        if bound.lower_bounded():
            ranks[str(bound.lower)] = len(bound.lower.version.tokens)
            if is_fixed_bound(bound):
                continue
        if bound.upper_bounded():
            ranks[str(bound.upper)] = len(bound.upper.version.tokens)

    return ranks


@contextmanager
def de_wildcard(request):
    pass


class WildcardVisitor(object):

    def __init__(self, request):
        self.cleaned_versions = dict()
        self.wildcard_map = dict()
        self._request = request

    def __enter__(self):
        from .requirement import Requirement

        request = self._request

        while "**" in request:
            uid = "_%s_" % uuid4().hex
            request = request.replace("**", uid, 1)
            self.wildcard_map[uid] = "**"

        while "*" in request:
            uid = "_%s_" % uuid4().hex
            request = request.replace("*", uid, 1)
            self.wildcard_map[uid] = "*"

        req = Requirement(request, invalid_bound_error=False)

    def __exit__(self, exc_type, exc_val, exc_tb):
        # cleanup
        pass

    def ranking(self):
        pass

    def _visit_version(self, version):
        cleaned_versions = self.cleaned_versions

        for v, cleaned_v in cleaned_versions.items():
            if version == next(v):
                return next(cleaned_v)

        version_ = self._clean_version(version)
        if version_ is None:
            return None

        cleaned_versions[version] = version_
        return version_

    def _clean_version(self, version):
        wildcard_map = self.wildcard_map
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
