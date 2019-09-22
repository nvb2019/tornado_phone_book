DROP TABLE IF EXISTS authors;
CREATE TABLE authors (
    id SERIAL PRIMARY KEY,
    email VARCHAR(100) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    hashed_password VARCHAR(100) NOT NULL
);

DROP TABLE IF EXISTS entries;
CREATE TABLE entries (
    id SERIAL PRIMARY KEY,
    author_id INT NOT NULL REFERENCES authors(id),
    slug VARCHAR(100) NOT NULL UNIQUE,
    full_name VARCHAR(512) NOT NULL,
    address VARCHAR(512) NOT NULL,
    telephone VARCHAR(512) NOT NULL,
    published TIMESTAMP NOT NULL,
    updated TIMESTAMP NOT NULL
);

CREATE INDEX ON entries (published);


