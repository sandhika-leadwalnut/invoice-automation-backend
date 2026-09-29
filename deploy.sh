#!/usr/bin/env bash
#
# Deploy the invoice backend on the server.
#
#     cd ~/invoice-automation/invoice-automation && ./deploy.sh
#
# Order matters and is the whole point of this script:
#
#   - the image is BUILT before the running container is touched, so a failed
#     build leaves the service up instead of taking it down
#   - the ingestion poller is paused across the swap, because workflow.py marks
#     an email read whether or not the backend accepted the invoice - anything
#     arriving while the backend is down is marked read and lost for good
#   - the previous image is tagged :prev first, and restored automatically if
#     the new one fails its health check
set -euo pipefail

SERVICE=processing_service
IMAGE=invoice-processing
PORT=8001
INGESTION=ingestion_service

cd "$(dirname "$0")"

run_container() {
    docker run -d \
        --name "$SERVICE" \
        --restart unless-stopped \
        -p "${PORT}:${PORT}" \
        -v "$(pwd)/.env:/app/.env" \
        -v "$(pwd)/tokens.json:/app/tokens.json" \
        -v /opt/invoice-uploads:/app/uploads \
        -e UPLOAD_DIR=/app/uploads \
        "$1" > /dev/null
}

echo "==> Checking disk"
AVAIL_MB=$(df --output=avail -BM / | tail -1 | tr -dc '0-9')
echo "    ${AVAIL_MB}MB free"
if [ "$AVAIL_MB" -lt 1500 ]; then
    echo "!!  Too little space - the build will fail part-way through."
    echo "    Free some first:  docker builder prune -f"
    exit 1
fi

echo "==> Pulling"
git pull

echo "==> Tagging the current image as :prev for rollback"
if docker image inspect "$IMAGE" > /dev/null 2>&1; then
    docker tag "$IMAGE" "${IMAGE}:prev"
fi

# If this fails the script stops here (set -e) and the running service, which
# has not been touched, keeps serving.
echo "==> Building"
docker build -t "$IMAGE" .

PAUSED=0
if docker ps --format '{{.Names}}' | grep -qx "$INGESTION"; then
    echo "==> Pausing ingestion so no invoice is lost during the swap"
    docker stop "$INGESTION" > /dev/null
    PAUSED=1
fi

echo "==> Replacing the container"
docker rm -f "$SERVICE" > /dev/null 2>&1 || true
run_container "$IMAGE"

echo "==> Waiting for it to answer"
OK=0
for _ in $(seq 1 15); do
    if curl -fsS "localhost:${PORT}/docs" > /dev/null 2>&1; then OK=1; break; fi
    sleep 1
done

if [ "$OK" -ne 1 ]; then
    echo "!!  It never came up. Rolling back to :prev."
    docker logs --tail 30 "$SERVICE" || true
    docker rm -f "$SERVICE" > /dev/null 2>&1 || true
    if docker image inspect "${IMAGE}:prev" > /dev/null 2>&1; then
        run_container "${IMAGE}:prev"
        echo "    Rolled back. The old version is running again."
    else
        echo "    No :prev image to roll back to - the service is DOWN."
    fi
    [ "$PAUSED" -eq 1 ] && docker start "$INGESTION" > /dev/null
    exit 1
fi

if [ "$PAUSED" -eq 1 ]; then
    echo "==> Restarting ingestion"
    docker start "$INGESTION" > /dev/null
fi

echo "==> Checks"
docker logs "$SERVICE" 2>&1 | grep -i "indexes" || echo "    (no index line - check the Atlas connection)"
docker exec "$SERVICE" python test_duplicates.py > /dev/null 2>&1 \
    && echo "    duplicate tests pass" || echo "    !! duplicate tests FAILED"
docker exec "$SERVICE" python test_payment_date.py > /dev/null 2>&1 \
    && echo "    payment date tests pass" || echo "    !! payment date tests FAILED"

echo
echo "Deployed. Rollback if needed:"
echo "    docker rm -f $SERVICE && docker tag ${IMAGE}:prev $IMAGE"
echo "    then re-run this script's docker run block, or ./deploy.sh from the previous commit"
