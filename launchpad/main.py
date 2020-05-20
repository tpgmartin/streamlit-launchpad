import tornado.ioloop
import tornado.web
import tornado.template
import os, signal
import tornado.process
import re
import click
from collections import namedtuple
from .dynamicapplication import DynamicApplication
from .handlers import ProxyWSHandler, ProxyHandler

AppInfo = namedtuple('AppInfo', 'name url')

# py file name to port number
proxymap = {}

port = 8500

# template_loader = tornado.template.Loader(os.path.join(os.path.dirname(os.path.realpath(__file__)),"templates"))

scan_folder_path = '../examples'


class MainHandler(tornado.web.RequestHandler):
    def get(self):
        apps = []
        for f in os.scandir(scan_folder_path):
            if f.name[-3:] == '.py':
                fname = ' '.join([f[0].upper() + f[1:] for f in f.name[:-3].split('_')])
                apps.append(AppInfo(name=fname, url="/{}/".format(f.name)))

        self.write(template_loader.load('main.html').generate(apps=apps, cwd=scan_folder_path))
        self.finish()


class DefaultProxyHandler(tornado.web.RequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        print("INIT PROXYHANDLER ***")

    async def get(self):
        match = re.search(r"^/(.+\.py)/(.*)$", self.request.path)
        appname, path = None, None
        if match:
            appname, path = match[1], match[2]
            print(match)

        else:
            print("NO MATCH: "+self.request.path)
            self.set_status(404)
            self.finish()
            return

        print("appname {}, path {}".format(appname, path))

        global proxymap
        my_port = 0
        if not appname in proxymap:

            global port

            def exit_callback(*args, **kwargs):
                print("exit callback {} {}".format(args, kwargs))
                self.application.remove_handlers(appname)
                proxymap[appname]['stopped'] = True

                proc = proxymap[appname]['proc']
                if proc:
                    if proc.stderr:
                        proxymap[appname]['stderr'] = proc.stderr
                    if proc.stdout:
                        proxymap[appname]['stdout'] = proc.stdout

            proc = tornado.process.Subprocess(['streamlit', 'run', os.path.join(scan_folder_path, appname),
                                               '--server.port', str(port),
                                               '--server.headless', 'True',
                                               '--server.runOnSave', 'True',
                                               '--server.enableCORS', 'False'],
                                              stdout=tornado.process.Subprocess.STREAM, stderr=tornado.process.Subprocess.STREAM,
                                              cwd=scan_folder_path)

            proc.set_exit_callback(exit_callback)

            proxymap[appname] = {'proc': proc, 'port': port, 'stopped': False, 'stdout': None, 'stderr': None}

            url = 'http://localhost:{}/'.format(port)

            self.application.add_handlers(
                r".*",
                [
                    (rf"^/{appname}/stream(.*)", ProxyWSHandler, {'proxy_url': url + 'stream'}, appname + 'ws'),
                    (rf"^/{appname}/(.*)", ProxyHandler, {'proxy_url': url}, appname + 'http')
                ])

            print("Started {}: {}".format(appname, port))

            my_port = port

            port += 1
        else:
            # We should only reach this point if remove_handlers was called on the app's underlying handlers,
            # so most likely the server has stopped
            my_port = proxymap[appname]['port']
            print("Already running {}: {}".format(appname, my_port))

            async def empty_and_close_stream(stream):
                if stream:
                    lines = await stream.read_until_close()
                    stream.close()
                    return lines
                return None

            stdout = await empty_and_close_stream(proxymap[appname]['stdout'])
            stderr = await empty_and_close_stream(proxymap[appname]['stderr'])

            del proxymap[appname]

            self.write(template_loader.load('error.html').generate(app=AppInfo(name=appname, url="/{}/".format(appname)),
                                                                   stdout=stdout, stderr=stderr, port=my_port))

            self.finish()
            return

        self.write(template_loader.load('loading.html').generate(app=AppInfo(name=appname, url="/{}/".format(appname)),
                                                                 port=my_port))

        self.finish()


def make_app():
    return DynamicApplication([
        (r"^/$", MainHandler),
    ],
    debug=True,
    default_handler_class=DefaultProxyHandler)


@click.command()
@click.option('--port', default=8888, help='port for the launchpad server')
@click.option('--templates', default='./launchpad/templates', help='folder for template views')
@click.argument('folder')
def run(port, templates, folder):
    global scan_folder_path, template_loader

    scan_folder_path = os.path.abspath(folder)
    templates = os.path.abspath(templates)
    template_loader = tornado.template.Loader(os.path.join(os.path.dirname(os.path.realpath(__file__)),templates))

    app = make_app()

    async def shutdown():
        tornado.ioloop.IOLoop.current().stop()

        for (appname, appval) in proxymap.items():
            if not appval['stopped']:
                proc = appval['proc']
                if proc:
                    print('Stopping proc for app {}'.format(appname))
                    proc.proc.terminate()

    def exit_handler(sig, frame):
        tornado.ioloop.IOLoop.current().add_callback_from_signal(shutdown)

    signal.signal(signal.SIGTERM, exit_handler)
    signal.signal(signal.SIGINT,  exit_handler)

    app.listen(port)
    print("Starting streamlit launchpad server of folder {} on port {}".format(scan_folder_path, port))
    tornado.ioloop.IOLoop.current().start()

if __name__ == '__main__':
    run()
