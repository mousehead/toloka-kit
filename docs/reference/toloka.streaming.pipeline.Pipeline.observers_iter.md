# observers_iter
`toloka.streaming.pipeline.Pipeline.observers_iter` | [Source code](https://github.com/Toloka/toloka-kit/blob/v0.1.24/src/streaming/pipeline.py#L193)

```python
observers_iter(self)
```

Iterate over registered observers.


* **Returns:**

  An iterator over all registered observers except deleted ones.
May contain observers sheduled to deletion and not deleted yet.

* **Return type:**

  Iterator\[[BaseObserver](toloka.streaming.observer.BaseObserver.md)\]
