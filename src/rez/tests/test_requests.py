
from rez.tests.util import TestBase
from rez.utils.request_directives import \
    DirectiveRequestParser, anonymous_directive


class TestRequest(TestBase):

    def test_wildcard_to_directive(self):
        requests = [
            # ("foo-**", "foo//harden", None),
            ("foo==**", "foo//harden", None),
            # ("foo-*", "foo//harden(1)", None),
            # ("foo-1.**", "foo-1//harden", None),
            # ("foo-1.0.*", "foo-1.0//harden(3)", None),
            # ("foo==*", "foo//harden(1)", None),
            # ("foo==1.*", "foo==1//harden(2)", None),
            # ("foo-1.*+", "foo-1+//harden(2)", None),
            # ("foo>1.*", "foo>1//harden(2)", None),
            # ("foo>=1.*", "foo>=1//harden(2)", None),
            # ("foo<**", "foo//harden", None),  # but meaningless
            # unsupported
            ("foo-2.*|1", None, "multi-rank hardening"),
            ("foo<=4,>2.*", None, "multi-rank hardening"),
            ("foo-1..2.*", None, "multi-rank hardening"),
            ("foo-1|2.*", None, "multi-rank hardening"),
            ("foo-1.*+<2.*.*", None, "multi-rank hardening"),
            ("foo-1.*+<3|==5.*.*", None, "multi-rank hardening"),
        ]

        for case, expected, message in requests:
            request = DirectiveRequestParser.parse(case)
            directive = anonymous_directive(request) or ""

            if expected is None:
                self.assertEqual(case, request, message)
            else:
                self.assertEqual(expected, "//".join([request, directive]))
