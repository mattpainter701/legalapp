from app.main import app


def _registered_routes():
    """Yield the effective routes served by FastAPI's lazy router wrappers."""
    for route in app.routes:
        contexts = getattr(route, "effective_route_contexts", None)
        entries = list(contexts()) if callable(contexts) else [route]
        for entry in entries:
            path = getattr(entry, "path", None)
            if path is not None:
                yield path, getattr(entry, "methods", None) or set()


def test_virtual_assistant_product_routes_are_registered():
    registered = {
        (path, method) for path, methods in _registered_routes() for method in methods
    }
    expected = {
        ("/api/intake/leads/{lead_id}/follow-through", "GET"),
        ("/api/intake/leads/{lead_id}/follow-through", "POST"),
        ("/api/intake/leads/{lead_id}/follow-through", "PATCH"),
        ("/api/intake/leads/{lead_id}/engagement-packets", "GET"),
        ("/api/intake/leads/{lead_id}/engagement-packets", "POST"),
        ("/api/intake/leads/{lead_id}/engagement-packets", "PATCH"),
        ("/api/intake/leads/{lead_id}/engagement-packets/render-preview", "POST"),
        ("/api/intake/leads/{lead_id}/engagement-packets/approve", "POST"),
        ("/api/platform/assistant/background-usage", "GET"),
        ("/api/platform/assistant/background-quota", "PUT"),
    }
    assert expected <= registered


def test_internal_prospect_prototype_router_is_not_exposed():
    assert not any(
        path.startswith("/api/prospect-follow-through")
        for path, _methods in _registered_routes()
    )
