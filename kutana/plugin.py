from kutana.tools.structures import objdict
import shlex
import re


class Plugin():
    """Class for creating extensions for kutana engine."""

    def __init__(self, **kwargs):
        self._callbacks = []
        self._callbacks_raw = []

        self._ecallbacks = []
        self._ecallbacks_raw = []

        self._callbacks_special = []

        self._callbacks_dispose = []
        self._callback_startup = None

        self.priority = 45

        for k, v in kwargs.items():
            setattr(self, k, v)

    @staticmethod
    def _done_if_none(value):
        """Return "DONE" if value is None. Otherwise return value."""

        if value is None:
            return "DONE"

        return value

    def _prepare_callbacks(self):
        """Return callbacks for registration in executor."""

        callbacks = []

        if self._callback_startup:
            async def wrapper_for_startup(update, eenv):
                await self._proc_startup(update, eenv)

            wrapper_for_startup.priority = self.priority

            callbacks.append(wrapper_for_startup)

        if self._ecallbacks or self._ecallbacks_raw:

            async def wrapper_for_early(update, eenv):
                return await self._proc_update(
                    update, eenv, (self._ecallbacks, self._ecallbacks_raw)
                )

            wrapper_for_early.priority = self.priority + 10

            callbacks.append(wrapper_for_early)

        if self._callbacks or self._callbacks_raw:

            async def wrapper(update, eenv):
                return await self._proc_update(
                    update, eenv, (self._callbacks, self._callbacks_raw)
                )

            wrapper.priority = self.priority

            callbacks.append(wrapper)

        callbacks += self._callbacks_special

        return callbacks

    async def _proc_startup(self, update, eenv):
        if eenv.ctrl_type != "kutana":
            return

        if update["update_type"] == "startup":
            if self._callback_startup:
                await self._callback_startup(update["kutana"], update)

    @staticmethod
    async def _proc_update(update, eenv, cbs=None):
        """Process update with eenv and target callbacks.
        If no callbacks passed raises RuntimeException.
        """

        if cbs is None:
            raise RuntimeError

        if eenv.ctrl_type == "kutana":
            return

        env = objdict(eenv=eenv, **eenv)

        if "_cached_message" in eenv:
            message = eenv["_cached_message"]

        else:
            message = await eenv.convert_to_message(update, eenv)

            eenv["_cached_message"] = message

        if message is None:
            if not cbs[1]:
                return

            arguments = {
                "env": env,
                "update": update
            }

            callbacks = cbs[1]

        else:
            arguments = {
                "env": env,
                "message": message,
                "attachments": message.attachments
            }

            callbacks = cbs[0]

        for callback in callbacks:
            comm = await callback(**arguments)

            if comm == "DONE":
                return "DONE"

    def register(self, *callbacks, early=False):
        """Register for processing updates in this plugin.

        If early is True, this callbacks will be executed
        before callbacks (from other plugins too) with `early=False`.
        """

        callbacks_list = self._ecallbacks if early else self._callbacks

        for callback in callbacks:
            callbacks_list.append(callback)

    def register_special(self, *callbacks, early=False):
        """Register callback for processing updates in this plugins's
        executor. Return decorator for registering callback.

        Arguments `env` and raw `update` is passed to callback.

        If `early` is True, this callbacks will be executed
        before callbacks (from other plugins too) with `early=False`.
        """

        def _register_special(callback):
            callback.priority = self.priority + 10 * early

            self._callbacks_special.append(callback)

        for callback in callbacks:
            _register_special(callback)

        return _register_special

    def on_dispose(self):
        """Returns decorator for adding callbacks which is triggered when
        everything is going to shutdown.
        """

        def decorator(coro):
            self._callbacks_dispose.append(coro)

            return coro

        return decorator

    def on_startup(self):
        """Returns decorator for adding callbacks which is triggered
        at the startup of kutana. Decorated coroutine receives kutana
        object and some information in update.
        """

        def decorator(coro):
            self._callback_startup = coro

            return coro

        return decorator

    def on_raw(self, early=False):
        """Returns decorator for adding callbacks which is triggered
        every time when update can't be turned into `Message` or
        `Attachment` object. Arguments `env` and raw `update`
        is passed to callback.

        See :func:`Plugin.register` for info about `early`.
        """

        def decorator(coro):
            if early:
                self._ecallbacks_raw.append(coro)

            self._callbacks_raw.append(coro)

            return coro

        return decorator

    def on_text(self, *texts, early=False):
        """Returns decorator for adding callbacks which is triggered
        when the message and any of the specified text are fully matched.

        See :func:`Plugin.register` for info about `early`.
        """

        def decorator(coro):
            check_texts = list(text.strip().lower() for text in texts)

            async def wrapper(*args, **kwargs):
                if kwargs["message"].text.strip().lower() in check_texts:
                    comm = self._done_if_none(await coro(*args, **kwargs))

                    if comm == "DONE":
                        return "DONE"

            self.register(wrapper, early=early)

            return wrapper

        return decorator

    def on_has_text(self, *texts, early=False):
        """Returns decorator for adding callbacks which is triggered
        when the message contains any of the specified texts.

        Fills env for callback with:

        - "found_text" - text found in message.

        See :func:`Plugin.register` for info about `early`.
        """

        def decorator(coro):
            check_texts = tuple(text.strip().lower() for text in texts) or ("",)

            async def wrapper(*args, **kwargs):
                check_text = kwargs["message"].text.strip().lower()

                for text in check_texts:
                    if text not in check_text:
                        continue

                    kwargs["env"]["found_text"] = text

                    comm = self._done_if_none(await coro(*args, **kwargs))

                    if comm == "DONE":
                        return "DONE"

            self.register(wrapper, early=early)

            return wrapper

        return decorator

    def on_startswith_text(self, *texts, early=False):
        """Returns decorator for adding callbacks which is triggered
        when the message starts with any of the specified texts.

        Fills env for callback with:

        - "body" - text without prefix.
        - "args" - text without prefix splitted in bash-like style.
        - "prefix" - prefix.

        See :func:`Plugin.register` for info about `early`.
        """

        def decorator(coro):
            check_texts = tuple(text.lstrip().lower() for text in texts)

            def search_prefix(message):
                for text in check_texts:
                    if message.startswith(text):
                        return text

                return None

            async def wrapper(*args, **kwargs):
                search_result = search_prefix(kwargs["message"].text.lower())

                if search_result is None:
                    return

                kwargs["env"]["body"] = kwargs["message"].text[len(search_result):].strip()
                kwargs["env"]["args"] = shlex.split(kwargs["env"]["body"])
                kwargs["env"]["prefix"] = kwargs["message"].text[:len(search_result)].strip()

                comm = self._done_if_none(await coro(*args, **kwargs))

                if comm == "DONE":
                    return "DONE"

            self.register(wrapper, early=early)

            return wrapper

        return decorator

    def on_regexp_text(self, regexp, flags=0, early=False):
        """Returns decorator for adding callbacks which is triggered
        when the message matches the specified regular expression.

        Fills env for callback with:

        - "match" - match.

        See :func:`Plugin.register` for info about `early`.
        """

        if isinstance(regexp, str):
            compiled = re.compile(regexp, flags=flags)

        else:
            compiled = regexp

        def decorator(coro):
            async def wrapper(*args, **kwargs):
                match = compiled.match(kwargs["message"].text)

                if not match:
                    return

                kwargs["env"]["match"] = match

                comm = self._done_if_none(await coro(*args, **kwargs))

                if comm == "DONE":
                    return "DONE"

            self.register(wrapper, early=early)

            return wrapper

        return decorator

    def on_attachment(self, *types, early=False):
        """Returns decorator for adding callbacks which is triggered
        when the message has attachments of the specified type
        (if no types specified, then any attachments).

        See :func:`Plugin.register` for info about `early`.
        """

        def decorator(coro):
            async def wrapper(*args, **kwargs):
                if not kwargs["attachments"]:
                    return

                if types:
                    for a in kwargs["attachments"]:
                        if a.type in types:
                            break
                    else:
                        return

                comm = self._done_if_none(await coro(*args, **kwargs))

                if comm == "DONE":
                    return "DONE"

            self.register(wrapper, early=early)

            return wrapper

        return decorator

    async def dispose(self):
        """Free resources and prepare for shutdown."""

        for callback in self._callbacks_dispose:
            await callback()
