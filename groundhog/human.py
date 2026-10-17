"""Text channel between the human operator and the running team: steering at any time, answers to questions.
Unattended runs (the evolve loop) have no Human, so agents decide alone and guards end with "unsolved"."""
import queue
import threading
import time


class Human:
    def __init__(self, ask_timeout_s: int = 900):
        self.lock, self.ask_lock = threading.Lock(), threading.Lock()
        self.events: list[dict] = []
        self.inbox: list[str] = []
        self.answers: queue.Queue = queue.Queue()
        self.waiting: str | None = None
        self.ask_timeout_s = ask_timeout_s

    def emit(self, ev: dict) -> None:
        with self.lock:
            self.events.append({**ev, "n": len(self.events), "ts": time.time()})

    def say(self, text: str) -> str:
        """From the UI: answers a pending question, otherwise steers the running agents."""
        if self.waiting:
            self.emit({"role": "human", "kind": "answer", "text": text})
            self.answers.put(text)
            return "answer"
        with self.lock:
            self.inbox.append(text)
        self.emit({"role": "human", "kind": "steer", "text": text})
        return "steer"

    def steering(self) -> list[str]:
        with self.lock:
            out, self.inbox = self.inbox, []
        return out

    def ask(self, question: str) -> str | None:
        with self.ask_lock:  # one question at a time
            self.waiting = question
            self.emit({"role": "agent", "kind": "question", "text": question})
            try:
                return self.answers.get(timeout=self.ask_timeout_s)
            except queue.Empty:
                return None
            finally:
                self.waiting = None
