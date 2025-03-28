Debugging and Logging
=====================

Below, we describe how to access log outputs and show a sample debugging flow.


Logging
~~~~~~~

There are three main ways to access logs:

(1) **On the cluster**

    Logs are automatically output onto the cluster, in the file ``~/.rh/server.log``. You can ssh
    into the cluster with ``runhouse cluster ssh cluster-name`` to view these logs.

(2) **Streaming**

    To see logs on your local machine while running a remote function, you can add the ``stream_logs=True``
    argument to your function call.

    .. code:: ipython3

        remote_fn = rh.function(fn)
        fn(fn_args, stream_logs=True)

(3) **Runhouse CLI**

    You can view the latest logs by running the command: ``runhouse cluster logs cluster-name``.

Log Levels
----------
You can set the log level to control the verbosity of the Runhouse logs. You can adjust the log level by setting the
environment variable ``RH_LOG_LEVEL`` to your desired level.

Debugging
~~~~~~~~~

For general debugging that doesn't occur within remote function calls, you can add ``breakpoint()`` wherever you want
to set your debugging session. If the code is being run locally at the point of the debugger, you'll be able to access
the session from your local machine. If the code is being run remotely on a cluster, you will need to ssh into the
cluster with ``runhouse cluster ssh cluster-name``, and then run ``screen -r`` inside the cluster.
From there, you will see the RPC logs being printed out, and can debug normally inside the ``screen``.

.. note::

    When debugging inside ``screen``, please use ``Ctrl A+D`` to exit out of the screen. Do NOT use ``Ctrl C``,
    which will terminate the RPC server.

    If you accidentally terminate the RPC server, you can run ``cluster.restart_server()`` to restart the
    server.

For debugging remote functions, which are launched using ``ray``, we can utilize Ray's debugger. Add a ``breakpoint()``
call inside the function where you want to start the debugging session, then ssh into the cluster with
``runhouse cluster ssh cluster-name``, and call ``ray debug`` to view select the breakpoint to enter.
You can run normal ``pdb`` commands within the debugging session, and can refer to `Ray Debugger
<https://docs.ray.io/en/latest/ray-contribute/debugging.html>`__ for more information.
