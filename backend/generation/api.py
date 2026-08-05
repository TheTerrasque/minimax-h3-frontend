"""DRF views for the generation app.

Only a health check is implemented in this pass, to prove the SPA -> nginx ->
backend path works end to end. Full CRUD for presets/jobs/references
(GET /api/presets/, POST /api/jobs/, POST /api/jobs/{id}/references/,
GET /api/queue-estimate/, POST /api/prompt/improve/, etc.) is deferred to the
next pass -- see ARCHITECTURE.md.
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"status": "ok"})
