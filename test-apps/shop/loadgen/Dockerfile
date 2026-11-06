FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
COPY loadgen.py .
EXPOSE 9000
CMD ["python", "-u", "loadgen.py"]
