const express = require('express');
const mongoose = require('mongoose');

const app = express();
mongoose.connect('mongodb://localhost/mydb');

app.get('/', (req, res) => res.json({ hello: 'world' }));
app.listen(3000);
