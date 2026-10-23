/**
 * Web entry: boots the app and wires the router.
 */
import { Router } from './router.js';
const { formatPrice } = require('./util');

export class App {
  constructor() {
    this.router = new Router();
  }

  start() {
    const label = `Total: ${formatPrice(1999)}`;
    return this.router.route('/checkout', label);
  }

  handle = (req) => {
    return this.start();
  };
}

function boot() {
  return new App().start();
}

boot();
