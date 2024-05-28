from django.conf import settings
from django.core import checks
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotFound,
)
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import TestCase, override_settings

from honeypot.checks import check_middleware_order
from honeypot.decorators import check_honeypot, honeypot_exempt, verify_honeypot_value
from honeypot.middleware import HoneypotResponseMiddleware, HoneypotViewMiddleware


def _get_GET_request():  # noqa: N802
    return HttpRequest()


def _get_POST_request():  # noqa: N802
    req = HttpRequest()
    req.method = "POST"
    return req


def view_func(request):
    return HttpResponse()


class HoneypotTestCase(TestCase):
    def setUp(self):
        if hasattr(settings, "HONEYPOT_VALUE"):
            delattr(settings, "HONEYPOT_VALUE")
        if hasattr(settings, "HONEYPOT_VERIFIER"):
            delattr(settings, "HONEYPOT_VERIFIER")
        if hasattr(settings, "HONEYPOT_RESPONDER"):
            delattr(settings, "HONEYPOT_RESPONDER")
        settings.HONEYPOT_FIELD_NAME = "honeypot"


class VerifyHoneypotValue(HoneypotTestCase):
    def test_no_call_on_get(self):
        """test that verify_honeypot_value is not called when request.method == GET"""
        request = _get_GET_request()
        resp = verify_honeypot_value(request, None)
        self.assertEqual(resp, None)

    def test_verifier_false(self):
        """test that verify_honeypot_value fails when HONEYPOT_VERIFIER returns False"""
        request = _get_POST_request()
        request.POST[settings.HONEYPOT_FIELD_NAME] = ""
        settings.HONEYPOT_VERIFIER = lambda x: False
        resp = verify_honeypot_value(request, None)
        self.assertEqual(resp.__class__, HttpResponseBadRequest)

    def test_field_missing(self):
        """test that verify_honeypot_value fails when HONEYPOT_FIELD_NAME is missing from
        request.POST"""
        request = _get_POST_request()
        resp = verify_honeypot_value(request, None)
        self.assertEqual(resp.__class__, HttpResponseBadRequest)

    def test_custom_responder(self):
        """test custom response, when verify_honeypot_value fails"""
        request = _get_POST_request()
        settings.HONEYPOT_RESPONDER = lambda x, y: HttpResponseNotFound()
        resp = verify_honeypot_value(request, None)
        self.assertEqual(resp.__class__, HttpResponseNotFound)

    def test_field_blank(self):
        """test that verify_honeypot_value succeeds when HONEYPOT_VALUE is blank"""
        request = _get_POST_request()
        request.POST[settings.HONEYPOT_FIELD_NAME] = ""
        resp = verify_honeypot_value(request, None)
        self.assertEqual(resp, None)

    def test_honeypot_value_string(self):
        """test that verify_honeypot_value succeeds when HONEYPOT_VALUE is the expected
        string"""
        request = _get_POST_request()
        settings.HONEYPOT_VALUE = "(test string)"
        request.POST[settings.HONEYPOT_FIELD_NAME] = settings.HONEYPOT_VALUE
        resp = verify_honeypot_value(request, None)
        self.assertEqual(resp, None)

    def test_honeypot_value_callable(self):
        """test that verify_honeypot_value succeeds when HONEYPOT_VALUE is the expected
        return value of a callable"""
        request = _get_POST_request()
        settings.HONEYPOT_VALUE = lambda: "(test string)"
        request.POST[settings.HONEYPOT_FIELD_NAME] = settings.HONEYPOT_VALUE()
        resp = verify_honeypot_value(request, None)
        self.assertEqual(resp, None)


class CheckHoneypotDecorator(HoneypotTestCase):
    def test_default_decorator(self):
        """test that @check_honeypot works and defaults to HONEYPOT_FIELD_NAME"""
        new_view_func = check_honeypot(view_func)
        request = _get_POST_request()
        resp = new_view_func(request)
        self.assertEqual(resp.__class__, HttpResponseBadRequest)

    def test_decorator_argument(self):
        """test that check_honeypot(view, 'fieldname') works"""
        new_view_func = check_honeypot(view_func, "fieldname")
        request = _get_POST_request()
        resp = new_view_func(request)
        self.assertEqual(resp.__class__, HttpResponseBadRequest)

    def test_decorator_py24_syntax(self):
        """test that @check_honeypot syntax works"""

        @check_honeypot("field")
        def new_view_func(request):
            return HttpResponse()

        request = _get_POST_request()
        resp = new_view_func(request)
        self.assertEqual(resp.__class__, HttpResponseBadRequest)


class RenderHoneypotField(HoneypotTestCase):
    def _assert_rendered_field(self, template, fieldname, value=""):
        correct = render_to_string(
            "honeypot/honeypot_field.html", {"fieldname": fieldname, "value": value}
        )
        rendered = template.render(Context())
        self.assertEqual(rendered, correct)

    def test_default_templatetag(self):
        """test that {% render_honeypot_field %} works and defaults to HONEYPOT_FIELD_NAME"""
        template = Template("{% load honeypot %}{% render_honeypot_field %}")
        self._assert_rendered_field(template, settings.HONEYPOT_FIELD_NAME, "")

    def test_templatetag_honeypot_value(self):
        """test that {% render_honeypot_field %} uses settings.HONEYPOT_VALUE"""
        template = Template("{% load honeypot %}{% render_honeypot_field %}")
        settings.HONEYPOT_VALUE = "(leave blank)"
        self._assert_rendered_field(
            template, settings.HONEYPOT_FIELD_NAME, settings.HONEYPOT_VALUE
        )

    def test_templatetag_argument(self):
        """test that {% render_honeypot_field 'fieldname' %} works"""
        template = Template(
            '{% load honeypot %}{% render_honeypot_field "fieldname" %}'
        )
        self._assert_rendered_field(template, "fieldname", "")


class HoneypotMiddleware(HoneypotTestCase):
    _response_body = '<form method="POST"></form>'

    def test_view_middleware_invalid(self):
        """don't call view when HONEYPOT_VERIFIER returns False"""
        request = _get_POST_request()
        retval = HoneypotViewMiddleware(lambda request: None).process_view(
            request, view_func, (), {}
        )
        self.assertEqual(retval.__class__, HttpResponseBadRequest)

    def test_view_middleware_valid(self):
        """call view when HONEYPOT_VERIFIER returns True"""
        request = _get_POST_request()
        request.POST[settings.HONEYPOT_FIELD_NAME] = ""
        retval = HoneypotViewMiddleware(lambda request: None).process_view(
            request, view_func, (), {}
        )
        self.assertEqual(retval, None)

    def test_response_middleware_rewrite(self):
        """ensure POST forms are rewritten"""
        request = _get_POST_request()
        request.POST[settings.HONEYPOT_FIELD_NAME] = ""
        response = HttpResponse(self._response_body)
        HoneypotResponseMiddleware(lambda request: response)(request)
        self.assertNotContains(response, self._response_body)
        self.assertContains(response, f'name="{settings.HONEYPOT_FIELD_NAME}"')

    def test_response_middleware_contenttype_exclusion(self):
        """ensure POST forms are not rewritten for non-html content types"""
        request = _get_POST_request()
        request.POST[settings.HONEYPOT_FIELD_NAME] = ""
        response = HttpResponse(self._response_body, content_type="text/javascript")
        HoneypotResponseMiddleware(lambda request: response)(request)
        self.assertContains(response, self._response_body)

    def test_response_middleware_unicode(self):
        """ensure that POST form rewriting works with unicode templates"""
        request = _get_GET_request()
        unicode_body = "\u2603" + self._response_body  # add unicode snowman
        response = HttpResponse(unicode_body)
        HoneypotResponseMiddleware(lambda request: response)(request)
        self.assertNotContains(response, unicode_body)
        self.assertContains(response, f'name="{settings.HONEYPOT_FIELD_NAME}"')

    def test_exempt_view(self):
        """call view no matter what if view is exempt"""
        request = _get_POST_request()
        exempt_view_func = honeypot_exempt(view_func)
        assert exempt_view_func.honeypot_exempt is True
        retval = HoneypotViewMiddleware(lambda request: None).process_view(
            request, exempt_view_func, (), {}
        )
        self.assertEqual(retval, None)


class HoneypotSystemChecks(TestCase):
    @override_settings(
        MIDDLEWARE=[
            "django.middleware.common.CommonMiddleware",
            "honeypot.middleware.HoneypotMiddleware",
        ]
    )
    def test_correct_order(self):
        errors = check_middleware_order(None)
        expected = []
        self.assertEqual(errors, expected)

    @override_settings(
        MIDDLEWARE=[
            "honeypot.middleware.HoneypotResponseMiddleware",
            "django.middleware.common.CommonMiddleware",
        ]
    )
    def test_wrong_order(self):
        errors = check_middleware_order(None)
        expected = [
            checks.Error(
                "The honeypot middleware needs to be listed after CommonMiddleware",
                id="honeypot.E001",
            )
        ]
        self.assertEqual(errors, expected)

    @override_settings(
        MIDDLEWARE=["django.contrib.sessions.middleware.SessionMiddleware"]
    )
    def test_not_in_middleware(self):
        errors = check_middleware_order(None)
        expected = []
        self.assertEqual(errors, expected)
