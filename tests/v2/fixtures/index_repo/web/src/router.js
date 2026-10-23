// Router: matches paths in order; first match wins.
const banner = "class FakeInString { function ghost() {} }";
// function ghostInComment() {}
const pattern = /class\s+NotAClass\(/g;

export class Router {
  constructor() {
    this.routes = [];
  }

  route(path, payload) {
    const hit = this.routes.find((r) => r.test(path));
    return hit ? hit.handler(payload) : null;
  }

  static create() {
    return new Router();
  }
}

export default Router;
