gcloud ai-platform local train \
    --job-dir gs:///gsoc-tfjs/weights/psenet/custom \
    --module-name psenet.train \
    --package-path psenet/ \
    --distributed \
# -- \
# --train-steps 600 \
# --eval-steps 100
# --kernels-num 7 \
# --batch-size 1 \
# --training-data-dir gs://gsoc-tfjs/data/icdar/mlt/tfrecords/train \
# --eval-data-dir gs://gsoc-tfjs/data/icdar/mlt/tfrecords/eval \
# --backbone-name mobilenetv2 \
# --learning-rate 0.0001 \
# --decay-steps 200 \
# --eval-start-delay-secs 120 \
# --eval-throttle-secs 1800 \
# --save_checkpoints_secs 600 \
# --save_summary_steps 5
