import asyncio
import momoko
import aiopg
import bcrypt
import markdown
import os.path
import psycopg2
import re
import tornado.escape
import tornado.httpserver
import tornado.ioloop
import tornado.locks
import tornado.options
import tornado.web
import unicodedata

from tornado.options import define, options

define("port", default=8888, help="run on the given port", type=int)
define("db_host", default="127.0.0.1", help="book database host")
define("db_port", default=5432, help="book database port")
define("db_database", default="test1", help="book database name")
define("db_user", default="test1_user", help="book database user")
define("db_password", default="test1", help="book database password")


class NoResultError(Exception):
    pass


async def maybe_create_tables(db):
    try:
        with (await db.cursor()) as cur:
            await cur.execute("SELECT COUNT(*) FROM entries LIMIT 1")
            await cur.fetchone()
    except psycopg2.ProgrammingError:
        with open("schema.sql") as f:
            schema = f.read()
        with (await db.cursor()) as cur:
            await cur.execute(schema)


class Application(tornado.web.Application):
    def __init__(self, db):
        self.db = db
        handlers = [
            (r"/", HomeHandler),
            (r"/archive", ArchiveHandler),
            (r"/feed", FeedHandler),
            (r"/entry/([^/]+)", EntryHandler),
            (r"/compose", ComposeHandler),
            (r"/clean_table", CleanHandler),
            (r"/auth/create", AuthCreateHandler),
            (r"/auth/login", AuthLoginHandler),
            (r"/auth/logout", AuthLogoutHandler),
            (r"/remove", RemoveHandler),
        ]
        settings = dict(
            blog_title=u"Tornado Phone Book",
            template_path=os.path.join(os.path.dirname(__file__), "templates"),
            static_path=os.path.join(os.path.dirname(__file__), "static"),
            ui_modules={"Entry": EntryModule},
            xsrf_cookies=True,
            cookie_secret="__TODO:_GENERATE_YOUR_OWN_RANDOM_VALUE_HERE__",
            login_url="/auth/login",
            debug=True,
        )
        super(Application, self).__init__(handlers, **settings)


class BaseHandler(tornado.web.RequestHandler):
    def row_to_obj(self, row, cur):
        """Преобразовывает строку SQL в объект, поддерживающий доступ к файлам dict и attribute."""
        obj = tornado.util.ObjectDict()
        for val, desc in zip(row, cur.description):
            obj[desc.name] = val
        return obj

    async def execute(self, stmt, *args):
        """Выполняет инструкцию SQL.
         Должен вызываться с помощью `` await self.execute (...) ``
        """
        with (await self.application.db.cursor()) as cur:
            await cur.execute(stmt, args)

    async def query(self, stmt, *args):
        """Запросить список результатов.
         Типичное использование::
             результаты = ожидание self.query (...)
         Или же::
             для строки в ожидании self.query (...)
        """
        with (await self.application.db.cursor()) as cur:
            await cur.execute(stmt, args)
            return [self.row_to_obj(row, cur) for row in await cur.fetchall()]

    async def queryone(self, stmt, *args):
        """Запрос только для одного результата.
         Вызывает NoResultError, если нет результатов, или ValueError, если
         их больше одного.
        """
        results = await self.query(stmt, *args)
        if len(results) == 0:
            raise NoResultError()
        elif len(results) > 1:
            raise ValueError("Expected 1 result, got %d" % len(results))
        return results[0]

    async def prepare(self):
        # get_current_user cannot be a coroutine, so set
        # self.current_user in prepare instead.
        user_id = self.get_secure_cookie("blogdemo_user")
        try:
            if user_id:
             self.current_user = await self.queryone(
                 "SELECT * FROM authors WHERE id = %s", int(user_id)
             )
        except NoResultError:
            a = 5


    async def any_author_exists(self):
        return bool(await self.query("SELECT * FROM authors LIMIT 1"))


class RemoveHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self):
        id = self.get_argument("id", None)

        try:
            await self.queryone("DELETE FROM entries WHERE id = %s", int(id))
        except psycopg2.ProgrammingError:
            self.redirect("/")

    #     id = self.get_argument("id", None)
    #     entry = None
    #     if id:
    #         entry = await self.queryone("DELETE * FROM entries WHERE id = %s", int(id))


class HomeHandler(BaseHandler):
    async def get(self):
        maybe_create_tables(self.application.db)
        entries = await self.query(
            "SELECT * FROM entries ORDER BY full_name ASC LIMIT 30"
        )
        if not entries:
            self.redirect("/compose")
            return
        self.render("home.html", entries=entries)


class EntryHandler(BaseHandler):
    async def get(self, slug):
        entry = await self.queryone("SELECT * FROM entries WHERE slug = %s", slug)
        if not entry:
            raise tornado.web.HTTPError(404)
        self.render("entry.html", entry=entry)


class ArchiveHandler(BaseHandler):
    async def get(self):
        entries = await self.query("SELECT * FROM entries ORDER BY published DESC")
        self.render("archive.html", entries=entries)


class FeedHandler(BaseHandler):
    async def get(self):
        entries = await self.query(
            "SELECT * FROM entries ORDER BY published DESC LIMIT 10"
        )
        self.set_header("Content-Type", "application/atom+xml")
        self.render("feed.xml", entries=entries)


class ComposeHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self):
        id = self.get_argument("id", None)
        entry = None
        if id:
            entry = await self.queryone("SELECT * FROM entries WHERE id = %s", int(id))
        self.render("compose.html", entry=entry)
        #self.redirect("/")

    @tornado.web.authenticated
    async def post(self):
        id = self.get_argument("id", None)
        full_name = self.get_argument("full_name")
        address = self.get_argument("address")
        telephone = self.get_argument("telephone")
        #text = self.get_argument("address")
        #html = markdown.markdown(text)
        if id:
            try:
                entry = await self.queryone(
                    "SELECT * FROM entries WHERE id = %s", int(id)
                )
            except NoResultError:
                raise tornado.web.HTTPError(404)
            slug = entry.slug
            await self.execute(
                "UPDATE entries SET full_name = %s, address = %s, telephone = %s "
                "WHERE id = %s",
                full_name,
                address,
                telephone,
                int(id),
            )
        else:
            slug = unicodedata.normalize("NFKD", full_name)
            slug = re.sub(r"[^\w]+", " ", slug)
            slug = "-".join(slug.lower().strip().split())
            slug = slug.encode("ascii", "ignore").decode("ascii")
            if not slug:
                slug = "entry"
            while True:
                e = await self.query("SELECT * FROM entries WHERE slug = %s", slug)
                if not e:
                    break
                slug += "-2"
            await self.execute(
                "INSERT INTO entries (author_id,full_name,slug,address,telephone,published,updated)"
                #"INSERT INTO entries (author_id,full_name,slug,address,telephone)"
                "VALUES (%s,%s,%s,%s,%s,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                #"VALUES (%s,%s,%s,%s,%s)",
                self.current_user.id,
                full_name,
                slug,
                address,
                telephone
            )
        self.redirect("/entry/" + slug)


class CleanHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self):
        try:
         entries = await self.query("DROP TABLE entries")
         maybe_create_tables(self.application.db)

        except psycopg2.ProgrammingError:
            maybe_create_tables(self.application.db)
            self.render("clean_table.html")


class AuthCreateHandler(BaseHandler):
    def get(self):
        self.render("create_user.html")

    async def post(self):
        if await self.any_author_exists():
            raise tornado.web.HTTPError(400, "author already created")
        hashed_password = await tornado.ioloop.IOLoop.current().run_in_executor(
            None,
            bcrypt.hashpw,
            tornado.escape.utf8(self.get_argument("password")),
            bcrypt.gensalt(),
        )
        author = await self.queryone(
            "INSERT INTO authors (email, name, hashed_password) "
            "VALUES (%s, %s, %s) RETURNING id",
            self.get_argument("email"),
            self.get_argument("name"),
            tornado.escape.to_unicode(hashed_password),
        )
        self.set_secure_cookie("blogdemo_user", str(author.id))
        self.redirect(self.get_argument("next", "/"))


class AuthLoginHandler(BaseHandler):
    async def get(self):
        # If there are no authors, redirect to the account creation page.
        if not await self.any_author_exists():
            self.redirect("/auth/create")
        else:
            self.render("login.html", error=None)

    async def post(self):
        try:
            author = await self.queryone(
                "SELECT * FROM authors WHERE email = %s", self.get_argument("email")
            )
        except NoResultError:
            self.render("login.html", error="email not found")
            return
        hashed_password = await tornado.ioloop.IOLoop.current().run_in_executor(
            None,
            bcrypt.hashpw,
            tornado.escape.utf8(self.get_argument("password")),
            tornado.escape.utf8(author.hashed_password),
        )
        hashed_password = tornado.escape.to_unicode(hashed_password)
        if hashed_password == author.hashed_password:
            self.set_secure_cookie("blogdemo_user", str(author.id))
            self.redirect(self.get_argument("next", "/"))
        else:
            self.render("login.html", error="incorrect password")


class AuthLogoutHandler(BaseHandler):
    def get(self):
        self.clear_cookie("blogdemo_user")
        self.redirect(self.get_argument("next", "/"))


class EntryModule(tornado.web.UIModule):
    def render(self, entry):
        return self.render_string("modules/entry.html", entry=entry)


async def main():
    tornado.options.parse_command_line()

    # Create the global connection pool.
    async with aiopg.create_pool(
        host=options.db_host,
        port=options.db_port,
        user=options.db_user,
        password=options.db_password,
        dbname=options.db_database,
    ) as db:


        await maybe_create_tables(db)
        app = Application(db)
        app.listen(options.port)

        # In this demo the server will simply run until interrupted
        # with Ctrl-C, but if you want to shut down more gracefully,
        # call shutdown_event.set().
        shutdown_event = tornado.locks.Event()
        await shutdown_event.wait()


if __name__ == "__main__":
    tornado.ioloop.IOLoop.current().run_sync(main)
