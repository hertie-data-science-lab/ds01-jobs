FROM alpine:latest
RUN echo "this should be rejected by scanner"
CMD echo "should never run"
